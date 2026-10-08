"""SENTINEL-X — pont caméra natif (Windows).

Pourquoi : sous Windows, Docker Desktop ne peut pas transmettre une webcam USB à un
conteneur (cf. guide API §9). Ce pont minimal lit la webcam EXTERNE sur l'hôte et
fournit les images brutes au conteneur d'IA, qui garde tout le traitement.

Sécurité :
- n'écoute QUE sur 127.0.0.1 (aucun accès depuis le réseau de table) ; les conteneurs
  y accèdent via le relais interne de Nginx (host.docker.internal) ;
- jeton secret obligatoire (en-tête X-Camera-Token, comparaison à temps constant) ;
- lecture seule : aucune commande, aucun fichier écrit, aucune IA ici ;
- caméra choisie par NOM (« UGREEN » par défaut) ; la webcam intégrée du PC est exclue.

Lancement : powershell -File scripts/start-camera-bridge.ps1
"""
import hmac
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import comtypes
import cv2
from pygrabber.dshow_graph import FilterGraph

ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"                     # volontairement non configurable
PORT = int(os.environ.get("BRIDGE_PORT", "8554"))
CAMERA_NAME = os.environ.get("CAMERA_NAME", "UGREEN")
CAMERA_EXCLUDE = re.compile(os.environ.get("CAMERA_EXCLUDE", r"integrated|intégrée|int[ée]gr"), re.I)
TOKEN_FILE = Path(os.environ.get("CAMERA_TOKEN_FILE", ROOT / "secrets" / "camera_token"))
WIDTH, HEIGHT, FPS = 640, 480, 15
MAX_CLIENTS = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s camera-bridge: %(message)s")
log = logging.getLogger("bridge")

TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()
if len(TOKEN) < 24:
    raise SystemExit(f"Jeton absent ou trop court : {TOKEN_FILE} (lancez scripts/setup.sh)")


def find_camera() -> tuple[int | None, str | None]:
    """Index DirectShow de la webcam externe (même ordre que cv2.CAP_DSHOW)."""
    devices = FilterGraph().get_input_devices()
    for index, name in enumerate(devices):
        if CAMERA_NAME.lower() in name.lower() and not CAMERA_EXCLUDE.search(name):
            return index, name
    log.warning("Caméra « %s » introuvable parmi : %s", CAMERA_NAME, devices)
    return None, None


class Camera:
    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.jpeg: bytes | None = None
        self.frame_id = 0
        self.name: str | None = None
        self.ok = False

    def run(self) -> None:
        comtypes.CoInitialize()          # DirectShow (COM) doit être initialisé dans ce thread
        while True:
            index, name = find_camera()
            if index is None:
                self.ok = False
                time.sleep(5)
                continue
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
            if not cap.isOpened():
                log.warning("Ouverture impossible de %s (index %d)", name, index)
                time.sleep(5)
                continue
            self.name = name
            log.info("Caméra ouverte : %s (index DirectShow %d)", name, index)
            period = 1.0 / FPS
            while True:
                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    log.warning("Lecture impossible, réouverture de la caméra")
                    break
                frame = cv2.resize(frame, (WIDTH, HEIGHT))
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    with self.cond:
                        self.jpeg = buf.tobytes()
                        self.frame_id += 1
                        self.ok = True
                        self.cond.notify_all()
                time.sleep(max(0.0, period - (time.monotonic() - t0)))
            self.ok = False
            cap.release()
            time.sleep(2)


camera = Camera()
clients = threading.BoundedSemaphore(MAX_CLIENTS)


class Handler(BaseHTTPRequestHandler):
    server_version = "camera-bridge"
    sys_version = ""

    def log_message(self, fmt, *args):      # journal sobre
        log.info("%s %s", self.address_string(), fmt % args)

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        token = self.headers.get("X-Camera-Token", "")
        if not hmac.compare_digest(token.encode(), TOKEN.encode()):
            return self._json(401, {"detail": "jeton invalide"})
        if self.path == "/health":
            return self._json(200, {"camera": camera.ok, "name": camera.name})
        if self.path != "/stream":
            return self._json(404, {"detail": "introuvable"})
        if not clients.acquire(blocking=False):
            return self._json(503, {"detail": "trop de clients"})
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = -1
            while True:
                with camera.cond:
                    camera.cond.wait_for(lambda: camera.frame_id != last, timeout=2)
                    jpeg, last = camera.jpeg, camera.frame_id
                if jpeg and camera.ok:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            clients.release()

    def do_POST(self):
        self._json(405, {"detail": "lecture seule"})

    do_PUT = do_DELETE = do_PATCH = do_POST


if __name__ == "__main__":
    threading.Thread(target=camera.run, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    log.info("Pont caméra à l'écoute sur http://%s:%d (local uniquement)", HOST, PORT)
    server.serve_forever()
