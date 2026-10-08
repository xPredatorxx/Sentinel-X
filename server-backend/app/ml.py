"""Maintenance prédictive : Isolation Forest (Scikit-Learn).

Le modèle est sérialisé avec joblib (pickle) : charger un fichier modifié par un
attaquant reviendrait à exécuter son code. Le fichier est donc signé (HMAC-SHA256)
à l'entraînement et la signature est vérifiée avant chaque chargement.
"""
import hashlib
import hmac
import io
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Telemetry
from .security import auth_store

log = logging.getLogger("sentinel.ml")

FEATURES = ["temp", "hum", "gas_raw"]
MIN_SAMPLES = 300          # ~10 min de mesures (une toutes les 2 s)
MAX_SAMPLES = 50_000


def _paths() -> tuple[Path, Path]:
    d = Path(settings.model_dir)
    return d / "anomaly_model.joblib", d / "anomaly_model.joblib.sig"


def _sign(data: bytes) -> str:
    return hmac.new(auth_store.model_signing_key(), data, hashlib.sha256).hexdigest()


class AnomalyModel:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: IsolationForest | None = None
        self._mtime: float | None = None
        self.trained_at: str | None = None
        self.samples: int | None = None
        self.last_error: str | None = None

    def train(self, db: Session) -> dict:
        rows = db.execute(
            select(Telemetry.temp, Telemetry.hum, Telemetry.gas_raw)
            .where(Telemetry.temp.is_not(None), Telemetry.hum.is_not(None), Telemetry.gas_raw.is_not(None))
            .order_by(Telemetry.id.desc())
            .limit(MAX_SAMPLES)
        ).all()
        if len(rows) < MIN_SAMPLES:
            raise ValueError(f"Pas assez de mesures pour entraîner le modèle ({len(rows)}/{MIN_SAMPLES})")

        df = pd.DataFrame(rows, columns=FEATURES)
        # n_estimators = 100 arbres ; contamination = part des mesures d'entraînement jugées anormales
        # (ML_CONTAMINATION, 0.01 par défaut ; 0.1 = 10 % : plus sensible mais beaucoup plus de fausses alertes)
        model = IsolationForest(n_estimators=100, contamination=settings.ml_contamination, random_state=42)
        model.fit(df[FEATURES])

        buf = io.BytesIO()
        joblib.dump(model, buf)
        data = buf.getvalue()
        model_path, sig_path = _paths()
        model_path.parent.mkdir(parents=True, exist_ok=True)
        # écriture atomique : le fichier n'est jamais lu à moitié écrit
        tmp = model_path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, model_path)
        sig_path.write_text(_sign(data), encoding="ascii")

        with self._lock:
            self._model = model
            self._mtime = model_path.stat().st_mtime
            self.samples = len(df)
            self.trained_at = datetime.now(timezone.utc).isoformat()
            self.last_error = None
        log.info("Modèle Isolation Forest entraîné sur %d mesures", len(df))
        return {"samples": len(df), "trained_at": self.trained_at}

    def _load_if_needed(self) -> IsolationForest | None:
        model_path, sig_path = _paths()
        if not model_path.is_file():
            return None
        mtime = model_path.stat().st_mtime
        if self._model is not None and self._mtime == mtime:
            return self._model
        data = model_path.read_bytes()
        expected = sig_path.read_text(encoding="ascii").strip() if sig_path.is_file() else ""
        if not hmac.compare_digest(_sign(data), expected):
            self.last_error = "Signature du modèle invalide : chargement refusé"
            log.error(self.last_error)
            self._model, self._mtime = None, mtime
            return None
        self._model = joblib.load(io.BytesIO(data))
        self._mtime = mtime
        self.trained_at = self.trained_at or datetime.fromtimestamp(mtime, timezone.utc).isoformat()
        self.last_error = None
        return self._model

    def predict(self, temp: float, hum: float, gas_raw: int) -> tuple[bool, float] | None:
        """Renvoie (anomalie ?, score) ou None si aucun modèle valide n'est disponible."""
        with self._lock:
            try:
                model = self._load_if_needed()
            except Exception:
                log.exception("Chargement du modèle impossible")
                self.last_error = "Chargement du modèle impossible"
                return None
            if model is None:
                return None
            x = pd.DataFrame([{"temp": temp, "hum": hum, "gas_raw": gas_raw}])[FEATURES]
            # predict() renvoie 1 (normal) ou -1 (anomalie)
            is_anomaly = int(model.predict(x)[0]) == -1
            score = float(model.decision_function(x)[0])
            return is_anomaly, score

    def status(self) -> dict:
        model_path, _ = _paths()
        with self._lock:
            try:
                loaded = self._load_if_needed() is not None
            except Exception:
                loaded = False
        return {
            "loaded": loaded,
            "trained_at": self.trained_at,
            "samples": self.samples,
            "file_present": model_path.is_file(),
            "error": self.last_error,
        }


anomaly_model = AnomalyModel()
_last_anomaly: dict[str, float] = {}


def anomaly_cooldown_ok(node_id: str) -> bool:
    """Évite une avalanche d'alertes : une alerte d'anomalie max par période."""
    now = time.monotonic()
    last = _last_anomaly.get(node_id)
    if last is not None and now - last < settings.anomaly_cooldown_s:
        return False
    _last_anomaly[node_id] = now
    return True
