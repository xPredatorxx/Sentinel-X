from functools import cached_property
from pathlib import Path
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict

# racine du repo = deux dossiers au-dessus de ce fichier (app/ -> server-backend/ -> racine)
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def read_secret(path: str | None) -> str | None:
    """Lit un secret depuis un fichier (Docker secrets : /run/secrets/<nom>).

    Les secrets ne passent jamais par des variables d'environnement : elles sont
    visibles avec `docker inspect` et héritées par tous les sous-processus.
    """
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    value = p.read_text(encoding="utf-8").strip()
    return value or None


class Settings(BaseSettings):
    # chemin absolu : le .env est trouvé quel que soit le dossier de lancement
    model_config = SettingsConfigDict(env_file=ENV_PATH, extra="ignore")

    # --- Base de données -------------------------------------------------
    # DATABASE_URL complète : uniquement pour le dev local / les tests.
    # En Docker, l'URL est construite à partir du fichier secret.
    database_url: str | None = None
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "sentinel"
    db_user: str = "sentinel_app"
    db_password_file: str | None = "/run/secrets/db_app_password"

    # --- MQTT (TLS obligatoire, même à l'intérieur du réseau Docker) ------
    mqtt_enabled: bool = True
    mqtt_host: str = "mqtt"
    mqtt_port: int = 8883
    mqtt_user: str = "api"
    mqtt_password_file: str | None = "/run/secrets/mqtt_api_password"
    mqtt_ca_file: str = "/run/certs/ca.crt"

    # --- Identités ---------------------------------------------------------
    # Liste blanche des boîtiers : un node_id inconnu est rejeté.
    known_nodes: str = "sentinel-edge-01"
    # node_id auquel la clé API de l'IA vision est liée
    vision_node_id: str = "sentinel-vision-01"
    ai_api_key_file: str | None = "/run/secrets/ai_api_key"
    ai_health_url: str = "http://ai:5000/health"

    # --- Authentification dashboard ----------------------------------------
    jwt_secret_file: str | None = "/run/secrets/jwt_secret"
    admin_username: str = "admin"
    admin_password_file: str | None = "/run/secrets/admin_password"
    viewer_username: str = "operateur"
    viewer_password_file: str | None = "/run/secrets/viewer_password"
    session_ttl_minutes: int = 480
    cookie_secure: bool = True
    # origines supplémentaires autorisées (ex. http://localhost:5173 en dev)
    allowed_origins: str = ""

    # --- Divers ------------------------------------------------------------
    docs_enabled: bool = False
    model_dir: str = "/data/models"
    max_clock_skew_s: int = 300
    anomaly_cooldown_s: int = 60
    # part d'anomalies supposée dans l'historique d'entraînement (0.01 = 1 %)
    ml_contamination: float = 0.01
    node_offline_after_s: int = 15

    @cached_property
    def known_node_set(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.known_nodes.split(",") if n.strip())

    @cached_property
    def extra_origins(self) -> frozenset[str]:
        return frozenset(o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip())

    @cached_property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        password = read_secret(self.db_password_file)
        if not password:
            raise RuntimeError("Mot de passe base de données introuvable (DB_PASSWORD_FILE)")
        return (
            f"postgresql+psycopg://{quote(self.db_user)}:{quote(password, safe='')}"
            f"@{self.db_host}:{self.db_port}/{quote(self.db_name)}"
        )


settings = Settings()
