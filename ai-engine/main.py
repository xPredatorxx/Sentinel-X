"""SENTINEL-X — IA vision (YOLOv8n + reconnaissance faciale YuNet/SFace).

Sécurité :
- aucun téléchargement à l'exécution : les modèles sont embarqués dans l'image et
  leur empreinte SHA-256 est vérifiée au démarrage (refus de démarrer sinon) ;
- aucune commande système, aucun eval : le service ne fait que lire la caméra ;
- aucune entrée utilisateur hormis deux routes GET sans paramètre ;
- les alertes partent vers l'API avec une clé API liée à un node_id et à des types
  d'alerte précis : l'API revalide tout (sortie de l'IA = donnée non fiable).
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sentinel.vision")

# ==========================================
# 0. CONFIGURATION
# ==========================================
MODEL_DIR = Path(os.environ.get("MODEL_DIR", Path(__file__).parent / "models"))
FACES_DIR = Path(os.environ.get("FACES_DIR", Path(__file__).parent / "visages_autorises"))
CAMERA_SOURCE = os.environ.get("CAMERA_SOURCE", "0")
# Flux du pont caméra natif (Windows), relayé par Nginx dans le réseau interne
CAMERA_URL = os.environ.get("CAMERA_URL", "")
CAMERA_TOKEN_FILE = os.environ.get("CAMERA_TOKEN_FILE", "/run/secrets/camera_token")
API_URL = os.environ.get("API_URL", "http://api:8000/api/v1/alerts")
API_KEY_FILE = os.environ.get("AI_API_KEY_FILE", "/run/secrets/ai_api_key")
NODE_ID = os.environ.get("NODE_ID", "sentinel-vision-01")
PERSON_CONF = float(os.environ.get("PERSON_CONF", "0.5"))
FACE_MATCH_THRESHOLD = 0.363          # seuil cosinus recommandé par OpenCV pour SFace
ALERT_COOLDOWN_S = float(os.environ.get("ALERT_COOLDOWN_S", "30"))
INTRUDER_FRAMES = int(os.environ.get("INTRUDER_FRAMES", "3"))   # trames consécutives avant alerte
AUTHORIZED_COOLDOWN_S = float(os.environ.get("AUTHORIZED_COOLDOWN_S", "120"))
YOLO_IMGSZ = int(os.environ.get("YOLO_IMGSZ", "640"))   # 320 pour un PC modeste
MAX_STREAMS = 4
FRAME_W, FRAME_H = 640, 480

YOLO_FILE = "yolov8n.pt"
YUNET_FILE = "face_detection_yunet.onnx"
SFACE_FILE = "face_recognition_sface.onnx"


def parse_camera_source(value: str):
    """Seuls un index de webcam ou un périphérique /dev/videoN sont acceptés (pas d'URL)."""
    if re.fullmatch(r"\d{1,2}", value):
        return int(value)
    if re.fullmatch(r"/dev/video\d{1,2}", value):
        return value
    raise ValueError(f"CAMERA_SOURCE invalide : {value!r}")


def parse_camera_url(value: str) -> str:
    """URL du pont caméra : HTTP vers un nom de service Docker interne uniquement
    (pas de point dans le nom => impossible de viser un hôte externe)."""
    if not re.fullmatch(r"http://[a-z][a-z0-9-]{0,62}(:\d{2,5})?/[A-Za-z0-9/_.-]{0,100}", value):
        raise ValueError(f"CAMERA_URL invalide : {value!r}")
    return value


class MjpegStream:
    """Lecteur MJPEG minimal avec la même interface que cv2.VideoCapture.

    Un thread lit le flux en continu et ne garde que la DERNIÈRE image : l'IA, plus lente
    que la caméra, analyse toujours l'instant présent (pas de retard qui s'accumule).
    Les données reçues sont bornées (image ≤ 2 Mo) et décodées par OpenCV uniquement.
    """
    MAX_FRAME = 2 * 1024 * 1024

    def __init__(self, url: str, token: str) -> None:
        self._alive = False
        req = urllib.request.Request(url, headers={"X-Camera-Token": token})
        try:
            self._resp = urllib.request.urlopen(req, timeout=5)
        except (OSError, urllib.error.URLError):
            self._resp = None
            return
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._read_seq = 0
        self._alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        buf = b""
        try:
            while self._alive:
                chunk = self._resp.read1(65536)
                if not chunk:
                    break
                buf += chunk
                end = buf.rfind(b"\xff\xd9")
                start = buf.rfind(b"\xff\xd8", 0, end) if end != -1 else -1
                if start != -1:
                    jpeg, buf = buf[start:end + 2], buf[end + 2:]
                    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        with self._cond:
                            self._frame, self._seq = img, self._seq + 1
                            self._cond.notify_all()
                if len(buf) > self.MAX_FRAME:
                    buf = b""                     # flux anormal : on jette
        except OSError:
            pass
        finally:
            self._alive = False
            with self._cond:
                self._cond.notify_all()

    def isOpened(self) -> bool:
        return self._resp is not None and self._alive

    def read(self):
        with self._cond:
            self._cond.wait_for(lambda: self._seq != self._read_seq or not self._alive, timeout=5)
            if self._seq == self._read_seq:
                return False, None
            self._read_seq = self._seq
            return True, self._frame

    def release(self) -> None:
        self._alive = False
        if self._resp is not None:
            self._resp.close()


def verify_models() -> None:
    """Vérifie l'empreinte de chaque modèle contre le manifeste models.sha256."""
    manifest = MODEL_DIR / "models.sha256"
    expected = {}
    for line in manifest.read_text().splitlines():
        if line.strip():
            digest, name = line.split()
            expected[name.lstrip("*")] = digest
    for name in (YOLO_FILE, YUNET_FILE, SFACE_FILE):
        h = hashlib.sha256((MODEL_DIR / name).read_bytes()).hexdigest()
        if h != expected.get(name):
            raise RuntimeError(f"Empreinte invalide pour {name} : modèle refusé")
    log.info("Empreintes des modèles vérifiées")


# ==========================================
# 1. INITIALISATION DES MOTEURS IA
# ==========================================
verify_models()
if CAMERA_URL:
    CAMERA = parse_camera_url(CAMERA_URL)
    try:
        CAMERA_TOKEN = Path(CAMERA_TOKEN_FILE).read_text().strip()
    except OSError:
        raise SystemExit("CAMERA_URL défini mais jeton du pont caméra introuvable")
else:
    CAMERA = parse_camera_source(CAMERA_SOURCE)


def open_camera():
    return MjpegStream(CAMERA, CAMERA_TOKEN) if CAMERA_URL else cv2.VideoCapture(CAMERA)

from ultralytics import YOLO  # noqa: E402  (import après vérification des modèles)

log.info("Chargement de YOLOv8 (détection corps humain)...")
yolo_model = YOLO(str(MODEL_DIR / YOLO_FILE))

log.info("Chargement de SFace (reconnaissance faciale)...")
face_detector = cv2.FaceDetectorYN.create(str(MODEL_DIR / YUNET_FILE), "", (320, 320), 0.9, 0.3, 5000)
face_recognizer = cv2.FaceRecognizerSF.create(str(MODEL_DIR / SFACE_FILE), "")

# ==========================================
# 2. APPRENTISSAGE DES VISAGES AUTORISÉS
# ==========================================
MAX_FACE_FILES = 20
MAX_FACE_BYTES = 15 * 1024 * 1024


def get_face_feature(image_path: Path):
    if image_path.stat().st_size > MAX_FACE_BYTES:
        return None
    img = cv2.imread(str(image_path))
    if img is None or img.ndim != 3:
        return None
    # Photos haute résolution (smartphone) : on réduit à 800 px max
    height, width, _ = img.shape
    if max(height, width) > 800:
        scale = 800 / max(height, width)
        img = cv2.resize(img, (int(width * scale), int(height * scale)))
        height, width, _ = img.shape
    face_detector.setInputSize((width, height))
    _, faces = face_detector.detect(img)
    if faces is None:
        return None
    face_align = face_recognizer.alignCrop(img, faces[0])
    return face_recognizer.feature(face_align)


authorized_faces: dict[str, np.ndarray] = {}
if FACES_DIR.is_dir():
    files = sorted(p for p in FACES_DIR.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.is_file())
    for path in files[:MAX_FACE_FILES]:
        # nom affiché = début du nom de fichier, réduit à des caractères sûrs
        name = re.sub(r"[^a-z0-9_-]", "", path.name.split(".")[0].lower())[:24] or "inconnu"
        feature = get_face_feature(path)
        if feature is not None:
            authorized_faces[name] = feature
            log.info("Visage autorisé chargé : %s", name)
        else:
            log.warning("Aucun visage net trouvé sur %s", path.name)


# ==========================================
# 3. ENVOI DES ALERTES À L'API
# ==========================================
def _api_key() -> str | None:
    try:
        return Path(API_KEY_FILE).read_text().strip() or None
    except OSError:
        return None


API_KEY = _api_key()
if not API_KEY:
    log.warning("Pas de clé API : les alertes ne seront pas transmises")


def post_alert(alert_type: str, severity: str, metrics: dict[str, float]) -> None:
    if not API_KEY:
        return
    body = json.dumps({
        "node_id": NODE_ID,
        "timestamp": int(time.time()),
        "alert_type": alert_type,
        "severity": severity,
        "metrics": metrics,
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-API-Key": API_KEY,
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            log.info("Alerte %s transmise (HTTP %d)", alert_type, r.status)
    except urllib.error.HTTPError as e:
        log.warning("Alerte refusée par l'API : HTTP %d", e.code)
    except OSError as e:
        log.warning("API injoignable : %s", type(e).__name__)


# ==========================================
# 4. BOUCLE D'INFÉRENCE (un seul thread possède la caméra)
# ==========================================
class Vision:
    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.jpeg: bytes | None = None
        self.frame_id = 0
        self.camera_ok = False
        self.fps = 0.0
        self.inference_ms = 0.0
        self._suspect_frames = 0
        self._authorized_frames = 0
        self._last_alert: dict[str, float] = {}

    def _publish(self, frame) -> None:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self.cond:
                self.jpeg = buf.tobytes()
                self.frame_id += 1
                self.cond.notify_all()

    @staticmethod
    def _placeholder() -> np.ndarray:
        img = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
        img[:] = (24, 20, 16)
        cv2.putText(img, "CAMERA HORS LIGNE", (150, 230), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (90, 90, 230), 2)
        cv2.putText(img, time.strftime("%H:%M:%S UTC", time.gmtime()), (245, 270), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 150, 150), 1)
        return img

    def analyse(self, frame: np.ndarray) -> np.ndarray:
        annotated = frame.copy()
        # --- A. DÉTECTION HUMAINE (YOLO, classe 0 = person) ---
        results = yolo_model.predict(frame, classes=[0], conf=PERSON_CONF, imgsz=YOLO_IMGSZ, verbose=False)
        persons = []
        for box in results[0].boxes:
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            conf = float(box.conf[0])
            persons.append(conf)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 160, 60), 2)
            cv2.putText(annotated, f"personne {conf:.0%}", (x1 + 4, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 160, 60), 1)

        # --- B. DÉTECTION ET IDENTIFICATION FACIALE (SFace), seulement si une personne est vue ---
        authorized_seen, unknown_faces, names = False, 0, []
        faces = None
        if persons:
            face_detector.setInputSize((frame.shape[1], frame.shape[0]))
            _, faces = face_detector.detect(frame)
        if faces is not None:
            for face in faces:
                box = list(map(int, face[:4]))
                feature = face_recognizer.feature(face_recognizer.alignCrop(frame, face))
                color, label = (0, 0, 255), "INTRUS : ACCES REFUSE"
                for name, ref in authorized_faces.items():
                    score = face_recognizer.match(feature, ref, cv2.FaceRecognizerSF_FR_COSINE)
                    if score >= FACE_MATCH_THRESHOLD:
                        color, label = (0, 200, 0), f"AUTORISE : {name.upper()}"
                        authorized_seen = True
                        names.append(name)
                        break
                else:
                    unknown_faces += 1
                cv2.rectangle(annotated, (box[0], box[1]), (box[0] + box[2], box[1] + box[3]), color, 3)
                cv2.rectangle(annotated, (box[0], box[1] - 26), (box[0] + 230, box[1]), color, -1)
                cv2.putText(annotated, label, (box[0] + 5, box[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        # --- C. DÉCISION, confirmée sur plusieurs trames (limite les faux positifs) ---
        # toute présence humaine déclenche une alerte :
        #   personne non identifiée  -> INTRUDER / CRITICAL
        #   visage(s) autorisé(s) seul(s) -> MOTION / INFO (présence autorisée, tracée)
        intruder = bool(persons) and (unknown_faces > 0 or not authorized_seen)
        authorized = bool(persons) and authorized_seen and unknown_faces == 0
        self._suspect_frames = self._suspect_frames + 1 if intruder else 0
        self._authorized_frames = self._authorized_frames + 1 if authorized else 0
        now = time.monotonic()
        metrics = {"confidence": round(max(persons), 3) if persons else 0.0, "persons": float(len(persons)),
                   "unknown_faces": float(unknown_faces)}
        if self._suspect_frames >= INTRUDER_FRAMES and now - self._last_alert.get("INTRUDER", -1e9) > ALERT_COOLDOWN_S:
            self._last_alert["INTRUDER"] = now
            threading.Thread(target=post_alert, args=("INTRUDER", "CRITICAL", metrics), daemon=True).start()
        if self._authorized_frames >= INTRUDER_FRAMES and now - self._last_alert.get("MOTION", -1e9) > AUTHORIZED_COOLDOWN_S:
            self._last_alert["MOTION"] = now
            metrics["authorized"] = 1.0
            threading.Thread(target=post_alert, args=("MOTION", "INFO", metrics), daemon=True).start()
        return annotated

    def run(self) -> None:
        camera = None
        last_try = 0.0
        frames, t_fps = 0, time.monotonic()
        while True:
            if camera is None or not camera.isOpened():
                self.camera_ok = False
                self._publish(self._placeholder())
                if time.monotonic() - last_try > 5:
                    last_try = time.monotonic()
                    camera = open_camera()
                    if camera.isOpened():
                        log.info("Caméra ouverte (%s)", "pont caméra" if CAMERA_URL else CAMERA)
                time.sleep(1)
                continue
            ok, frame = camera.read()
            if not ok or frame is None or frame.ndim != 3 or frame.shape[2] != 3:
                log.warning("Lecture caméra impossible, nouvelle tentative")
                camera.release()
                camera = None
                continue
            self.camera_ok = True
            frame = cv2.resize(frame, (FRAME_W, FRAME_H))
            t0 = time.perf_counter()
            try:
                annotated = self.analyse(frame)
            except Exception:
                log.exception("Erreur d'inférence")
                annotated = frame
            self.inference_ms = round((time.perf_counter() - t0) * 1000, 1)
            self._publish(annotated)
            frames += 1
            if time.monotonic() - t_fps >= 2:
                self.fps = round(frames / (time.monotonic() - t_fps), 1)
                frames, t_fps = 0, time.monotonic()


vision = Vision()
threading.Thread(target=vision.run, name="vision", daemon=True).start()
streams = threading.BoundedSemaphore(MAX_STREAMS)

# ==========================================
# 5. SERVEUR HTTP (réseau Docker interne uniquement, derrière Nginx)
# ==========================================
app = Flask(__name__)


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp


class FrameStream:
    """Flux MJPEG : toutes les connexions partagent la dernière image calculée.

    close() est appelé par le serveur WSGI à la déconnexion, même si aucune image
    n'a encore été envoyée : la place dans le sémaphore est toujours libérée.
    """

    def __init__(self) -> None:
        self._closed = False

    def __iter__(self):
        last_id = -1
        while True:
            with vision.cond:
                vision.cond.wait_for(lambda: vision.frame_id != last_id, timeout=2)
                jpeg, last_id = vision.jpeg, vision.frame_id
            if jpeg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            streams.release()


@app.get("/video_feed")
def video_feed():
    if not streams.acquire(blocking=False):
        return jsonify(detail="Trop de flux ouverts"), 503
    return Response(FrameStream(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.get("/health")
def health():
    return jsonify(camera=vision.camera_ok, model=True, fps=vision.fps, inference_ms=vision.inference_ms,
                   faces=len(authorized_faces))


if __name__ == "__main__":
    # dev uniquement : en Docker, gunicorn sert l'application
    app.run(host="127.0.0.1", port=5000, threaded=True)
