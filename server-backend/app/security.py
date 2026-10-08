"""Authentification, autorisation et limitation de débit.

- Dashboard : session dans un cookie HttpOnly + Secure + SameSite=Strict
  (jamais de jeton dans le localStorage : inaccessible au JavaScript, donc à une XSS).
- Scripts IA : clé API (en-tête X-API-Key) liée à un node_id et à des types d'alerte.
- Rôles : admin (commandes, entraînement IA), viewer (lecture seule), ai (POST /alerts).
"""
import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock
from urllib.parse import urlsplit

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, WebSocket, status

from .config import read_secret, settings
from .schemas import AlertType

log = logging.getLogger("sentinel.security")

SESSION_COOKIE = "sentinel_session"
JWT_ALG = "HS256"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class Principal:
    name: str
    role: str                      # admin | viewer | ai
    allowed_nodes: frozenset[str] = field(default_factory=frozenset)
    allowed_alerts: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_user(self) -> bool:
        return self.role in ("admin", "viewer")


class AuthStore:
    """Charge les secrets une seule fois et garde uniquement des empreintes en mémoire."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher()
        self._users: dict[str, tuple[str, str]] = {}     # username -> (hash argon2, rôle)
        self._ai_key_digest: bytes | None = None
        self._jwt_secret: str | None = None
        # empreinte factice : on calcule un hash même si l'utilisateur n'existe pas,
        # pour ne pas révéler par le temps de réponse quels comptes existent.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    def load(self) -> None:
        self._jwt_secret = read_secret(settings.jwt_secret_file)
        if not self._jwt_secret or len(self._jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET_FILE manquant ou trop court (32 caractères minimum)")
        for username, path, role in (
            (settings.admin_username, settings.admin_password_file, "admin"),
            (settings.viewer_username, settings.viewer_password_file, "viewer"),
        ):
            pw = read_secret(path)
            if pw:
                if len(pw) < 12:
                    raise RuntimeError(f"Mot de passe du compte {role} trop court (12 caractères minimum)")
                self._users[username] = (self._hasher.hash(pw), role)
        if not self._users:
            raise RuntimeError("Aucun compte dashboard configuré")
        ai_key = read_secret(settings.ai_api_key_file)
        if ai_key:
            self._ai_key_digest = hashlib.sha256(ai_key.encode()).digest()

    @property
    def jwt_secret(self) -> str:
        assert self._jwt_secret
        return self._jwt_secret

    def verify_user(self, username: str, password: str) -> str | None:
        entry = self._users.get(username)
        try:
            self._hasher.verify(entry[0] if entry else self._dummy_hash, password)
        except (VerifyMismatchError, VerificationError):
            return None
        return entry[1] if entry else None

    def verify_ai_key(self, key: str) -> bool:
        if not self._ai_key_digest:
            return False
        return hmac.compare_digest(hashlib.sha256(key.encode()).digest(), self._ai_key_digest)

    def model_signing_key(self) -> bytes:
        # clé dérivée (séparation de domaine) pour signer le fichier du modèle IA
        return hmac.new(self.jwt_secret.encode(), b"sentinel-model-signing-v1", hashlib.sha256).digest()


auth_store = AuthStore()


# --- Sessions -------------------------------------------------------------------

def create_session_token(username: str, role: str) -> str:
    now = int(time.time())
    payload = {"sub": username, "role": role, "iat": now, "exp": now + settings.session_ttl_minutes * 60}
    return jwt.encode(payload, auth_store.jwt_secret, algorithm=JWT_ALG)


def decode_session_token(token: str | None) -> Principal | None:
    if not token or len(token) > 2048:
        return None
    try:
        # algorithms explicite : empêche l'attaque "alg: none"
        data = jwt.decode(token, auth_store.jwt_secret, algorithms=[JWT_ALG], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    role = data.get("role")
    if role not in ("admin", "viewer"):
        return None
    return Principal(name=str(data["sub"]), role=role)


# --- Contrôle d'origine (anti-CSRF / anti cross-site WebSocket hijacking) ---------

def origin_allowed(origin: str | None, host: str | None) -> bool:
    if not origin:
        return False
    origin = origin.rstrip("/")
    if origin in settings.extra_origins:
        return True
    parts = urlsplit(origin)
    return parts.scheme in ("https", "http") and bool(host) and parts.netloc == host


def client_ip(request: Request) -> str:
    # X-Real-IP est écrasé par Nginx (jamais repris du client) ; seuls Nginx et
    # l'IA peuvent joindre l'API (réseau Docker interne).
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


# --- Dépendances FastAPI -------------------------------------------------------------

def _principal_from_request(request: Request) -> Principal | None:
    api_key = request.headers.get("x-api-key")
    if api_key:
        if auth_store.verify_ai_key(api_key):
            return Principal(
                name="ai-vision",
                role="ai",
                allowed_nodes=frozenset({settings.vision_node_id}),
                allowed_alerts=frozenset({AlertType.INTRUDER.value, AlertType.MOTION.value}),
            )
        log.warning("Clé API invalide depuis %s", client_ip(request))
        return None

    principal = decode_session_token(request.cookies.get(SESSION_COOKIE))
    if principal and request.method in UNSAFE_METHODS:
        # Requête modifiante avec cookie : on exige la même origine ET un en-tête
        # personnalisé (impossible à envoyer cross-origin sans pré-requête CORS).
        if not origin_allowed(request.headers.get("origin"), request.headers.get("host")) \
                or request.headers.get("x-requested-with") != "sentinel-dashboard":
            log.warning("Requête %s refusée (origine invalide) depuis %s", request.url.path, client_ip(request))
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Origine refusée")
    return principal


def require_principal(request: Request) -> Principal:
    principal = _principal_from_request(request)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentification requise")
    return principal


def require_user(principal: Principal = Depends(require_principal)) -> Principal:
    if not principal.is_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accès refusé")
    return principal


def require_admin(principal: Principal = Depends(require_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Droits administrateur requis")
    return principal


def require_alert_writer(principal: Principal = Depends(require_principal)) -> Principal:
    if principal.role not in ("ai", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accès refusé")
    return principal


def websocket_principal(ws: WebSocket) -> Principal | None:
    if not origin_allowed(ws.headers.get("origin"), ws.headers.get("host")):
        return None
    principal = decode_session_token(ws.cookies.get(SESSION_COOKIE))
    return principal if principal and principal.is_user else None


# --- Limitation de débit (en mémoire, fenêtre glissante) -----------------------------

class RateLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str) -> bool:
        """Enregistre un appel ; renvoie False si la limite est dépassée."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:            # borne mémoire
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
            return True

    def blocked(self, key: str) -> bool:
        """True si la clé a déjà atteint la limite (sans compter un nouvel appel)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return False
            while q and now - q[0] > self.window_s:
                q.popleft()
            return len(q) >= self.limit

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


login_failures = RateLimiter(limit=5, window_s=15 * 60)
api_limiter = RateLimiter(limit=120, window_s=60)
alert_limiter = RateLimiter(limit=30, window_s=60)
command_limiter = RateLimiter(limit=20, window_s=60)


def rate_limit(limiter: RateLimiter, scope: str):
    def dependency(request: Request) -> None:
        if not limiter.hit(f"{scope}:{client_ip(request)}"):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de requêtes", headers={"Retry-After": "60"})
    return dependency
