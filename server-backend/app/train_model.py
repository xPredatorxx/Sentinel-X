"""Entraînement manuel du modèle :  docker compose exec api python -m app.train_model

(Également disponible depuis le dashboard : Administration > Entraîner le modèle.)
"""
import sys

from .db import SessionLocal
from .ml import anomaly_model
from .security import auth_store


def main() -> int:
    print("MISSION SENTINEL - entraînement de l'Isolation Forest")
    auth_store.load()   # nécessaire pour signer le modèle
    with SessionLocal() as db:
        try:
            result = anomaly_model.train(db)
        except ValueError as e:
            print(f"Échec : {e}")
            return 1
    print(f"Modèle entraîné sur {result['samples']} mesures et signé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
