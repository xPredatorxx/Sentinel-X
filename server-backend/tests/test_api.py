"""Tests de sécurité et de contrat de l'API (SQLite en mémoire, MQTT désactivé).

Lancement :  cd server-backend && python -m pytest -q
"""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp())
for name, value in {
    "jwt_secret": "x" * 48,
    "admin_password": "admin-password-123",
    "viewer_password": "viewer-password-123",
    "ai_api_key": "ai-key-0123456789abcdef",
}.items():
    (_tmp / name).write_text(value)

os.environ.update({
    "DATABASE_URL": f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    "MQTT_ENABLED": "false",
    "JWT_SECRET_FILE": str(_tmp / "jwt_secret"),
    "ADMIN_PASSWORD_FILE": str(_tmp / "admin_password"),
    "VIEWER_PASSWORD_FILE": str(_tmp / "viewer_password"),
    "AI_API_KEY_FILE": str(_tmp / "ai_api_key"),
    "MODEL_DIR": str(_tmp / "models"),
    "KNOWN_NODES": "sentinel-edge-01",
    "AI_HEALTH_URL": "http://127.0.0.1:9/health",
})

from fastapi.testclient import TestClient  # noqa: E402

from app import security  # noqa: E402
from app.main import app  # noqa: E402
from app.services import mqtt_bridge  # noqa: E402

AI = {"X-API-Key": "ai-key-0123456789abcdef"}
BASE = "https://testserver"
CSRF = {"Origin": BASE, "X-Requested-With": "sentinel-dashboard"}
ALERT = {"node_id": "sentinel-vision-01", "alert_type": "INTRUDER", "severity": "CRITICAL",
         "metrics": {"confidence": 0.91}}


@pytest.fixture()
def client():
    for limiter in (security.login_failures, security.api_limiter, security.alert_limiter, security.command_limiter):
        limiter._hits.clear()
    with TestClient(app, base_url=BASE) as c:
        yield c


def login(c, user="admin", pw="admin-password-123"):
    r = c.post("/api/v1/auth/login", json={"username": user, "password": pw}, headers={"Origin": BASE})
    assert r.status_code == 200, r.text
    return r


# --- Contrat d'interface ---------------------------------------------------------------

def test_health_is_minimal(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_post_valid_alert_returns_201(client):
    r = client.post("/api/v1/alerts", json=ALERT, headers=AI)
    assert r.status_code == 201
    assert r.json()["status"] == "ACK" and r.json()["incident_id"].startswith("INC-")


def test_unknown_enum_returns_422_with_allowed_values(client):
    r = client.post("/api/v1/alerts", json={**ALERT, "alert_type": "FIRE"}, headers=AI)
    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert err["loc"] == ["body", "alert_type"] and "INTRUDER" in err["msg"]


@pytest.mark.parametrize("body", [
    {**ALERT, "extra": 1},                                   # champ inconnu
    {**ALERT, "node_id": "<script>"},                        # caractères interdits
    {**ALERT, "node_id": "x" * 200},                         # trop long
    {**ALERT, "metrics": {"confidence": "high"}},            # mauvais type
    {**ALERT, "metrics": {"Bad Key!": 1}},                   # clé invalide
    {**ALERT, "timestamp": "hier"},                          # mauvais type
    {k: v for k, v in ALERT.items() if k != "severity"},     # champ manquant
])
def test_invalid_payloads_rejected(client, body):
    assert client.post("/api/v1/alerts", json=body, headers=AI).status_code == 422


def test_malformed_json_rejected(client):
    r = client.post("/api/v1/alerts", content=b'{"node_id": ', headers={**AI, "Content-Type": "application/json"})
    assert r.status_code == 422


def test_nan_rejected(client):
    raw = json.dumps(ALERT).replace("0.91", "NaN")
    r = client.post("/api/v1/alerts", content=raw, headers={**AI, "Content-Type": "application/json"})
    assert r.status_code == 422


def test_wrong_content_type_415(client):
    r = client.post("/api/v1/alerts", content=json.dumps(ALERT), headers={**AI, "Content-Type": "text/plain"})
    assert r.status_code == 415


def test_body_too_large_413(client):
    big = {**ALERT, "metrics": {"a": 1.0}, "pad": "x" * 20000}
    r = client.post("/api/v1/alerts", json=big, headers=AI)
    assert r.status_code == 413


def test_client_timestamp_not_trusted(client):
    r = client.post("/api/v1/alerts", json={**ALERT, "timestamp": 1000}, headers=AI)
    assert r.status_code == 422
    r = client.post("/api/v1/alerts", json={**ALERT, "timestamp": int(time.time())}, headers=AI)
    assert r.status_code == 201


def test_confidence_bounds(client):
    r = client.post("/api/v1/alerts", json={**ALERT, "metrics": {"confidence": 7}}, headers=AI)
    assert r.status_code == 422


# --- Authentification / autorisation -------------------------------------------------------

def test_alert_requires_auth(client):
    assert client.post("/api/v1/alerts", json=ALERT).status_code == 401
    assert client.post("/api/v1/alerts", json=ALERT, headers={"X-API-Key": "wrong"}).status_code == 401


def test_ai_key_bound_to_node_and_types(client):
    r = client.post("/api/v1/alerts", json={**ALERT, "node_id": "sentinel-edge-01"}, headers=AI)
    assert r.status_code == 403
    r = client.post("/api/v1/alerts", json={**ALERT, "alert_type": "GAS_LEAK"}, headers=AI)
    assert r.status_code == 403


def test_ai_key_cannot_read_or_command(client):
    assert client.get("/api/v1/alerts", headers=AI).status_code == 403
    r = client.post("/api/v1/actuators/control", json={"node_id": "sentinel-edge-01", "action": "ALARM_ON"}, headers=AI)
    assert r.status_code == 403


def test_reads_require_session(client):
    for path in ("/api/v1/alerts", "/api/v1/telemetry/latest", "/api/v1/telemetry/history",
                 "/api/v1/system/status", "/api/v1/nodes", "/api/v1/auth/me"):
        assert client.get(path).status_code == 401, path


def test_login_sets_secure_httponly_cookie(client):
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert client.get("/api/v1/auth/me").json() == {"username": "admin", "role": "admin"}


def test_login_failure_generic_and_locked_out(client):
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
        assert r.status_code == 401 and r.json()["detail"] == "Identifiants invalides"
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin-password-123"})
    assert r.status_code == 429


def test_password_not_echoed_in_422(client):
    r = client.post("/api/v1/auth/login", json={"username": "bad user!", "password": "secret-value"})
    assert r.status_code == 422 and "secret-value" not in r.text


def test_forged_jwt_rejected(client):
    import jwt as pyjwt
    forged = pyjwt.encode({"sub": "admin", "role": "admin", "exp": 9999999999}, "wrong-secret-" * 4, algorithm="HS256")
    client.cookies.set("sentinel_session", forged)
    assert client.get("/api/v1/auth/me").status_code == 401
    none_alg = pyjwt.encode({"sub": "admin", "role": "admin", "exp": 9999999999}, None, algorithm="none")
    client.cookies.set("sentinel_session", none_alg)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_viewer_cannot_command_or_train(client):
    login(client, "operateur", "viewer-password-123")
    body = {"node_id": "sentinel-edge-01", "action": "ALARM_ON"}
    assert client.post("/api/v1/actuators/control", json=body, headers=CSRF).status_code == 403
    assert client.post("/api/v1/ml/train", headers=CSRF).status_code == 403
    assert client.get("/api/v1/alerts").status_code == 200


def test_csrf_protection_on_cookie_requests(client):
    login(client)
    body = {"node_id": "sentinel-edge-01", "action": "ALARM_ON"}
    # pas d'en-tête personnalisé
    assert client.post("/api/v1/actuators/control", json=body, headers={"Origin": BASE}).status_code == 403
    # origine étrangère
    r = client.post("/api/v1/actuators/control", json=body,
                    headers={"Origin": "https://evil.example", "X-Requested-With": "sentinel-dashboard"})
    assert r.status_code == 403


def test_command_202_when_broker_up(client, monkeypatch):
    async def fake_send(node_id, action):
        return 42
    monkeypatch.setattr(mqtt_bridge, "send_command", fake_send)
    login(client)
    r = client.post("/api/v1/actuators/control", json={"node_id": "sentinel-edge-01", "action": "BEEP_2"}, headers=CSRF)
    assert r.status_code == 202 and r.json()["command_id"] == 42


def test_command_validation(client):
    login(client)
    r = client.post("/api/v1/actuators/control", json={"node_id": "sentinel-edge-01", "action": "rm -rf /"}, headers=CSRF)
    assert r.status_code == 422
    r = client.post("/api/v1/actuators/control", json={"node_id": "other-node", "action": "BEEP_2"}, headers=CSRF)
    assert r.status_code == 404
    r = client.post("/api/v1/actuators/control", json={"node_id": "sentinel-edge-01", "action": "BEEP_2"}, headers=CSRF)
    assert r.status_code == 503      # broker absent dans les tests


def test_sql_injection_is_just_data(client):
    login(client)
    r = client.get("/api/v1/telemetry/latest", params={"node_id": "x' OR '1'='1"})
    assert r.status_code == 404
    r = client.get("/api/v1/telemetry/history", params={"range": "1h; DROP TABLE alerts"})
    assert r.status_code == 422


def test_no_stack_trace_on_500(client, monkeypatch):
    from app import main
    def boom():
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(main.anomaly_model, "status", boom)
    login(client)
    c = TestClient(app, base_url=BASE, raise_server_exceptions=False)
    c.cookies = client.cookies
    r = c.get("/api/v1/ml/status")
    assert r.status_code == 500 and "secret internal detail" not in r.text and "Traceback" not in r.text


def test_docs_disabled_by_default(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


# --- WebSocket ---------------------------------------------------------------------------------

def test_websocket_requires_session_and_origin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/live", headers={"Origin": BASE}) as ws:
            ws.receive_json()
    login(client)
    # le client de test ouvre ws:// : le cookie Secure n'y est pas joint automatiquement
    # (le navigateur, lui, l'envoie sur wss://), on le passe donc explicitement.
    cookie = {"Cookie": f"sentinel_session={client.cookies.get('sentinel_session')}"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/live", headers={"Origin": "https://evil.example", **cookie}) as ws:
            ws.receive_json()
    with client.websocket_connect("/ws/live", headers={"Origin": BASE, **cookie}) as ws:
        assert ws.receive_json()["type"] == "hello"


# --- Ingestion MQTT --------------------------------------------------------------------------------

def _telemetry(seq, uptime=10_000, **kw):
    return json.dumps({"seq": seq, "uptime_ms": uptime, "temp": 22.5, "hum": 40.0, "gas_raw": 800,
                       "gas_v": 0.9, "motion": False, "rssi": -60, **kw}).encode()


def test_mqtt_ingest_validation_and_replay(client):
    from app.services import counters
    run = lambda topic, payload: asyncio.run(mqtt_bridge.handle(topic, payload))  # noqa: E731
    saved = counters.telemetry_saved
    rejected = counters.mqtt_rejected

    run("sentinel/sentinel-edge-01/telemetry", _telemetry(1, 1000))
    assert counters.telemetry_saved == saved + 1
    run("sentinel/sentinel-edge-01/telemetry", _telemetry(1, 2000))           # rejeu
    run("sentinel/intrus-99/telemetry", _telemetry(2, 2000))                  # node inconnu
    run("sentinel/sentinel-edge-01/telemetry", b"not json")                   # JSON invalide
    run("sentinel/sentinel-edge-01/telemetry", _telemetry(3, 3000, temp=999))  # hors plage
    run("sentinel/sentinel-edge-01/telemetry", _telemetry(4, 4000, evil="x"))  # champ inconnu
    run("sentinel/sentinel-edge-01/telemetry", b"{" + b" " * 5000 + b"}")     # trop gros
    run("other/topic", b"{}")
    assert counters.telemetry_saved == saved + 1
    assert counters.mqtt_rejected == rejected + 7
    run("sentinel/sentinel-edge-01/telemetry", _telemetry(1, 50))             # redémarrage : accepté
    assert counters.telemetry_saved == saved + 2

    login(client)
    latest = client.get("/api/v1/telemetry/latest").json()
    assert latest["node_id"] == "sentinel-edge-01" and latest["temp"] == 22.5
    hist = client.get("/api/v1/telemetry/history", params={"range": "15m"}).json()
    assert len(hist["points"]) >= 2


def test_ml_model_signature_enforced(client):
    from app.ml import anomaly_model, _paths
    from app.db import SessionLocal
    from app.models import Telemetry
    with SessionLocal() as db:
        for i in range(320):
            db.add(Telemetry(node_id="sentinel-edge-01", seq=1000 + i, uptime_ms=i, temp=22 + (i % 3) * 0.1,
                             hum=40.0, gas_raw=800 + i % 5, motion=False, rssi=-60))
        db.commit()
    login(client)
    r = client.post("/api/v1/ml/train", headers=CSRF)
    assert r.status_code == 200 and r.json()["samples"] >= 300
    assert anomaly_model.predict(22.0, 40.0, 800) is not None
    # falsification du fichier : le modèle doit être refusé
    model_path, _ = _paths()
    model_path.write_bytes(model_path.read_bytes() + b"tamper")
    os.utime(model_path, (time.time() + 5, time.time() + 5))
    assert anomaly_model.predict(22.0, 40.0, 800) is None
    assert "Signature" in (anomaly_model.status()["error"] or "")
