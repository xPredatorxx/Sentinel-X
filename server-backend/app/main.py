import asyncio
import logging
import math
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import models  # noqa: F401  (il faut importer models pour que les tables soient connues)
from .config import settings
from .db import Base, engine, get_db
from .ml import anomaly_model
from .models import Alert, Telemetry
from .schemas import (
    ActuatorCommand, AlertAck, AlertIn, AlertOut, AlertType, CommandAccepted, CommandAction, HistoryRange, LoginIn,
    TelemetryOut, UserOut,
)
from .security import (
    SESSION_COOKIE, Principal, alert_limiter, api_limiter, auth_store, client_ip, command_limiter,
    create_session_token, login_failures, rate_limit, require_admin, require_alert_writer, require_user,
    websocket_principal,
)
from .services import STARTED_AT, alert_to_out, counters, hub, mqtt_bridge, node_summary, store_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sentinel.api")

MAX_BODY_BYTES = 16 * 1024
background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth_store.load()                       # échoue au démarrage si un secret manque
    Base.metadata.create_all(engine)        # crée les tables si elles n'existent pas encore
    if engine.dialect.name == "postgresql":
        # mise à niveau d'une base créée par une version précédente (colonnes ajoutées)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS client_ts BIGINT"))
            conn.execute(text("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS source VARCHAR(32) DEFAULT 'unknown'"))
    if settings.mqtt_enabled:
        task = asyncio.create_task(mqtt_bridge.run())
        background_tasks.add(task)          # référence forte : la tâche n'est pas ramassée par le GC
        task.add_done_callback(background_tasks.discard)
    yield
    for task in list(background_tasks):
        task.cancel()


app = FastAPI(
    title="SENTINEL-X API",
    version="1.0.0",
    lifespan=lifespan,
    # documentation désactivée par défaut : on n'expose pas la carte de l'API
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)


# --- Durcissement HTTP ------------------------------------------------------------------

@app.middleware("http")
async def hardening(request: Request, call_next):
    # 1. taille du corps bornée (Nginx le fait aussi, mais l'IA appelle l'API en direct)
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        return JSONResponse({"detail": "Corps de requête trop volumineux"}, status_code=413)
    # 2. JSON uniquement pour les requêtes avec corps
    if request.method in ("POST", "PUT", "PATCH") and length and length != "0":
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return JSONResponse({"detail": "Content-Type application/json requis"}, status_code=415)
    # 3. limite globale par IP (hors sonde de santé)
    if request.url.path != "/health" and not api_limiter.hit(f"api:{client_ip(request)}"):
        return JSONResponse({"detail": "Trop de requêtes"}, status_code=429, headers={"Retry-After": "60"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    # jamais de trace d'appel renvoyée au client : elle reste dans les logs serveur
    log.exception("Erreur interne sur %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Erreur interne du serveur"}, status_code=500)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # même format que FastAPI (contrat §6.3), sans renvoyer le champ "ctx" interne
    errors = []
    for err in exc.errors()[:20]:
        item = {k: err[k] for k in ("type", "loc", "msg") if k in err}
        value = err.get("input")
        # on ne renvoie que des valeurs simples et courtes, jamais le mot de passe saisi
        if "password" not in err.get("loc", ()):
            if isinstance(value, str):
                item["input"] = value[:100]
            elif isinstance(value, (bool, int)) or value is None:
                item["input"] = value
            elif isinstance(value, float) and math.isfinite(value):
                item["input"] = value
        errors.append(item)
    return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


# --- Santé ---------------------------------------------------------------------------------

@app.get("/health")
def health(db: Session = Depends(get_db)):
    # sonde publique : aucune information interne
    try:
        db.execute(text("select 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="indisponible")
    return {"status": "ok"}


# --- Authentification ----------------------------------------------------------------------

@app.post("/api/v1/auth/login", response_model=UserOut, dependencies=[Depends(rate_limit(api_limiter, "login"))])
async def login(body: LoginIn, request: Request, response: Response):
    key = f"{client_ip(request)}:{body.username.lower()}"
    if login_failures.blocked(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives, réessayez plus tard",
                            headers={"Retry-After": "900"})
    role = await asyncio.to_thread(auth_store.verify_user, body.username, body.password)
    if role is None:
        login_failures.hit(key)
        log.warning("Échec de connexion pour %r depuis %s", body.username, client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides")
    login_failures.reset(key)
    log.info("Connexion de %s (%s) depuis %s", body.username, role, client_ip(request))
    response.set_cookie(
        SESSION_COOKIE, create_session_token(body.username, role),
        max_age=settings.session_ttl_minutes * 60, httponly=True, secure=settings.cookie_secure,
        samesite="strict", path="/",
    )
    return UserOut(username=body.username, role=role)


@app.post("/api/v1/auth/logout", status_code=204)
def logout(response: Response, user: Principal = Depends(require_user)):
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict")


@app.get("/api/v1/auth/me", response_model=UserOut)
def me(user: Principal = Depends(require_user)):
    return UserOut(username=user.name, role=user.role)


@app.get("/api/v1/auth/check", status_code=204)
def auth_check(user: Principal = Depends(require_user)):
    # utilisé par Nginx (auth_request) pour protéger le flux vidéo
    return Response(status_code=204)


# --- Alertes ---------------------------------------------------------------------------------

@app.post("/api/v1/alerts", response_model=AlertAck, status_code=201,
          dependencies=[Depends(rate_limit(alert_limiter, "alerts"))])
async def create_alert(alert: AlertIn, principal: Principal = Depends(require_alert_writer)):
    # node_id : on ne le croit pas sur parole
    if principal.role == "ai":
        if alert.node_id not in principal.allowed_nodes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "node_id non autorisé pour cette clé")
        if alert.alert_type.value not in principal.allowed_alerts:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Type d'alerte non autorisé pour cette clé")
    elif alert.node_id not in settings.known_node_set | {settings.vision_node_id}:
        raise HTTPException(422, "node_id inconnu")

    # horodatage client : contrôlé mais jamais utilisé comme référence
    if alert.timestamp is not None and abs(alert.timestamp - time.time()) > settings.max_clock_skew_s:
        raise HTTPException(422, "timestamp hors de la fenêtre tolérée")

    if "confidence" in alert.metrics and not 0.0 <= alert.metrics["confidence"] <= 1.0:
        raise HTTPException(422, "confidence doit être comprise entre 0 et 1")

    out = await asyncio.to_thread(
        store_alert, alert.node_id, alert.alert_type, alert.severity, principal.name, alert.metrics, alert.timestamp
    )
    # Diffuser l'alerte à tous les navigateurs (WebSocket)
    await hub.broadcast({"type": "alert", "data": out.model_dump(mode="json")})
    # intrus confirmé par la caméra : 2 bips sur le(s) boîtier(s) (décision serveur, alerte déjà limitée par l'IA)
    if alert.alert_type == AlertType.INTRUDER:
        for node in sorted(settings.known_node_set):
            await mqtt_bridge.beep(node, CommandAction.BEEP_2)
    return AlertAck(status="ACK", incident_id=out.incident_id)


@app.get("/api/v1/alerts", response_model=list[AlertOut])
def list_alerts(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db),
                user: Principal = Depends(require_user)):
    rows = db.scalars(select(Alert).order_by(Alert.id.desc()).limit(limit)).all()
    return [alert_to_out(r) for r in rows]


# --- Télémétrie --------------------------------------------------------------------------------

def _check_node(node_id: str) -> str:
    if node_id not in settings.known_node_set:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Boîtier inconnu")
    return node_id


def _default_node() -> str:
    return sorted(settings.known_node_set)[0]


@app.get("/api/v1/nodes")
def list_nodes(user: Principal = Depends(require_user)):
    return [node_summary(n) for n in sorted(settings.known_node_set)]


@app.get("/api/v1/telemetry/latest", response_model=TelemetryOut | None)
def telemetry_latest(node_id: str | None = Query(None, max_length=32), db: Session = Depends(get_db),
                     user: Principal = Depends(require_user)):
    node = _check_node(node_id or _default_node())
    row = db.scalars(
        select(Telemetry).where(Telemetry.node_id == node).order_by(Telemetry.id.desc()).limit(1)
    ).first()
    return TelemetryOut.model_validate(row, from_attributes=True) if row else None


RANGES = {
    HistoryRange.m15: timedelta(minutes=15),
    HistoryRange.h1: timedelta(hours=1),
    HistoryRange.h6: timedelta(hours=6),
    HistoryRange.h24: timedelta(hours=24),
}
MAX_POINTS = 300


@app.get("/api/v1/telemetry/history")
def telemetry_history(range: HistoryRange = Query(HistoryRange.h1), node_id: str | None = Query(None, max_length=32),
                      db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    node = _check_node(node_id or _default_node())
    since = datetime.now(timezone.utc) - RANGES[range]
    rows = db.execute(
        select(Telemetry.received_at, Telemetry.temp, Telemetry.hum, Telemetry.gas_raw, Telemetry.motion)
        .where(Telemetry.node_id == node, Telemetry.received_at >= since)
        .order_by(Telemetry.received_at)
    ).all()
    # sous-échantillonnage simple : au plus MAX_POINTS points pour les courbes
    step = max(1, len(rows) // MAX_POINTS)
    points = [
        {"t": r.received_at.isoformat(), "temp": r.temp, "hum": r.hum, "gas_raw": r.gas_raw, "motion": r.motion}
        for r in rows[::step]
    ]
    return {"node_id": node, "range": range.value, "points": points}


# --- Commandes actionneurs ----------------------------------------------------------------------

@app.post("/api/v1/actuators/control", response_model=CommandAccepted, status_code=202,
          dependencies=[Depends(rate_limit(command_limiter, "cmd"))])
async def actuator_control(cmd: ActuatorCommand, request: Request, admin: Principal = Depends(require_admin)):
    _check_node(cmd.node_id)
    try:
        cmd_id = await mqtt_bridge.send_command(cmd.node_id, cmd.action)
    except ConnectionError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Broker MQTT indisponible")
    log.info("Commande %s par %s depuis %s", cmd.action.value, admin.name, client_ip(request))
    # 202 : l'ordre est transmis, la confirmation arrivera par WebSocket (statut du boîtier)
    return CommandAccepted(command_id=cmd_id, node_id=cmd.node_id, action=cmd.action)


# --- IA prédictive ------------------------------------------------------------------------------

@app.get("/api/v1/ml/status")
def ml_status(user: Principal = Depends(require_user)):
    return anomaly_model.status()


@app.post("/api/v1/ml/train")
async def ml_train(db: Session = Depends(get_db), admin: Principal = Depends(require_admin)):
    try:
        result = await asyncio.to_thread(anomaly_model.train, db)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    log.info("Modèle réentraîné par %s", admin.name)
    return result


# --- Supervision (MCO) ----------------------------------------------------------------------------

_ai_cache: dict = {"at": 0.0, "value": None}


async def _ai_status() -> dict:
    if time.monotonic() - _ai_cache["at"] < 10 and _ai_cache["value"] is not None:
        return _ai_cache["value"]
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(settings.ai_health_url)
            data = r.json() if r.status_code == 200 else {}
        # sortie de l'IA = donnée non fiable : on ne garde que des champs connus et typés
        value = {
            "up": r.status_code == 200,
            "camera": bool(data.get("camera")),
            "model": bool(data.get("model")),
            "fps": float(data["fps"]) if isinstance(data.get("fps"), (int, float)) else None,
            "inference_ms": float(data["inference_ms"]) if isinstance(data.get("inference_ms"), (int, float)) else None,
        }
    except Exception:
        value = {"up": False, "camera": False, "model": False, "fps": None, "inference_ms": None}
    _ai_cache.update(at=time.monotonic(), value=value)
    return value


@app.get("/api/v1/system/status")
async def system_status(user: Principal = Depends(require_user)):
    def db_info():
        from .db import SessionLocal
        try:
            with SessionLocal() as db:
                db.execute(text("select 1"))
                return {
                    "up": True,
                    "alerts": db.scalar(select(func.count()).select_from(Alert)),
                    "telemetry": db.scalar(select(func.count()).select_from(Telemetry)),
                }
        except Exception:
            return {"up": False, "alerts": None, "telemetry": None}

    db, ai = await asyncio.gather(asyncio.to_thread(db_info), _ai_status())
    return {
        "api": {"up": True, "uptime_s": int(time.time() - STARTED_AT), "version": app.version},
        "database": db,
        "mqtt": {"up": mqtt_bridge.connected, "error": mqtt_bridge.last_error},
        "ai_vision": ai,
        "ml": anomaly_model.status(),
        "nodes": [node_summary(n) for n in sorted(settings.known_node_set)],
        "websocket_clients": hub.size,
        "counters": counters.__dict__,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


# --- Temps réel -------------------------------------------------------------------------------------

@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    # authentification AVANT accept() : cookie de session + origine identique
    principal = websocket_principal(websocket)
    if principal is None:
        await websocket.close(code=1008)    # policy violation
        return
    await websocket.accept()
    hub.add(websocket)
    try:
        await websocket.send_json({"type": "hello", "user": principal.name, "role": principal.role,
                                   "nodes": [node_summary(n) for n in sorted(settings.known_node_set)]})
        while True:
            # flux en lecture seule : les commandes passent par POST /api/v1/actuators/control.
            # On lit seulement pour détecter la déconnexion ; tout message est ignoré.
            msg = await websocket.receive_text()
            if len(msg) > 256:
                await websocket.close(code=1009)
                break
    except WebSocketDisconnect:
        pass
    finally:
        hub.discard(websocket)
