"""Logique métier partagée : stockage des alertes, diffusion temps réel, pont MQTT."""
import asyncio
import json
import logging
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiomqtt
from fastapi import WebSocket
from pydantic import ValidationError

from .config import read_secret, settings
from .db import SessionLocal
from .ml import anomaly_cooldown_ok, anomaly_model
from .models import Alert, Telemetry
from .schemas import AlertOut, AlertType, CommandAction, Severity, StatusIn, TelemetryIn

log = logging.getLogger("sentinel.services")

MAX_MQTT_PAYLOAD = 1024
STARTED_AT = time.time()


@dataclass
class Counters:
    mqtt_received: int = 0
    mqtt_rejected: int = 0
    telemetry_saved: int = 0
    alerts_created: int = 0
    commands_sent: int = 0
    last_rejection: str | None = None


counters = Counters()


# --- Alertes --------------------------------------------------------------------------

def alert_to_out(row: Alert) -> AlertOut:
    return AlertOut(
        incident_id=row.incident_id or "",
        node_id=row.node_id,
        timestamp=row.received_at,
        alert_type=row.alert_type,
        severity=row.severity,
        source=row.source,
        metrics=row.metrics or {},
    )


def store_alert(node_id: str, alert_type: AlertType, severity: Severity, source: str,
                metrics: dict[str, float], client_ts: int | None = None) -> AlertOut:
    with SessionLocal() as db:
        row = Alert(
            node_id=node_id,
            alert_type=alert_type.value,
            severity=severity.value,
            source=source,
            metrics=metrics,
            client_ts=client_ts,
            received_at=datetime.now(timezone.utc),
        )
        db.add(row)        # prépare l'insertion
        db.flush()         # envoie à la base pour obtenir l'id auto, sans valider encore
        row.incident_id = f"INC-{row.id:04d}"
        db.commit()        # valide définitivement
        counters.alerts_created += 1
        return alert_to_out(row)


# --- Diffusion WebSocket --------------------------------------------------------------

class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    @property
    def size(self) -> int:
        return len(self._clients)

    def add(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def discard(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


hub = Hub()


# --- État des boîtiers ------------------------------------------------------------------

@dataclass
class NodeState:
    online: bool | None = None
    alarm: bool | None = None
    last_seen: float | None = None             # time.monotonic()
    last_seen_at: datetime | None = None
    last_seq: int | None = None
    last_uptime: int | None = None
    last_motion: bool = False
    last_cmd: dict | None = None
    window: list[float] = field(default_factory=list)


nodes: dict[str, NodeState] = {}


def node_state(node_id: str) -> NodeState:
    return nodes.setdefault(node_id, NodeState())


def node_summary(node_id: str) -> dict:
    st = nodes.get(node_id) or NodeState()
    age = time.monotonic() - st.last_seen if st.last_seen else None
    fresh = age is not None and age < settings.node_offline_after_s
    return {
        "node_id": node_id,
        "online": bool(st.online) and fresh,
        "last_seen": st.last_seen_at.isoformat() if st.last_seen_at else None,
        "age_s": round(age, 1) if age is not None else None,
        "alarm": st.alarm,
        "last_cmd": st.last_cmd,
    }


# --- Pont MQTT ------------------------------------------------------------------------

class MqttBridge:
    def __init__(self) -> None:
        self.client: aiomqtt.Client | None = None
        self.connected = False
        self.last_error: str | None = None
        self._cmd_id = int(time.time()) % 100_000

    def _tls_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=settings.mqtt_ca_file)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx    # check_hostname=True et CERT_REQUIRED par défaut

    async def run(self) -> None:
        password = read_secret(settings.mqtt_password_file)
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=settings.mqtt_host,
                    port=settings.mqtt_port,
                    username=settings.mqtt_user,
                    password=password,
                    tls_context=self._tls_context(),
                    identifier="sentinel-api",
                    keepalive=30,
                ) as client:
                    self.client, self.connected, self.last_error = client, True, None
                    log.info("Connecté au broker MQTT (TLS)")
                    await client.subscribe("sentinel/+/telemetry", qos=0)
                    await client.subscribe("sentinel/+/status", qos=1)
                    async for message in client.messages:
                        try:
                            await self.handle(message.topic.value, message.payload)
                        except Exception:
                            log.exception("Erreur de traitement MQTT")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = type(e).__name__
                log.warning("Connexion MQTT perdue (%s), nouvelle tentative dans 5 s", type(e).__name__)
            finally:
                self.client, self.connected = None, False
            await asyncio.sleep(5)

    def _reject(self, reason: str) -> None:
        counters.mqtt_rejected += 1
        counters.last_rejection = reason
        log.warning("Message MQTT rejeté : %s", reason)

    async def handle(self, topic: str, payload) -> None:
        counters.mqtt_received += 1
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "sentinel" or parts[2] not in ("telemetry", "status"):
            return self._reject(f"topic inattendu {topic[:80]!r}")
        node_id, kind = parts[1], parts[2]
        # le node_id vient du topic, que l'ACL du broker lie au compte MQTT ; on vérifie
        # en plus qu'il fait partie de la liste blanche.
        if node_id not in settings.known_node_set:
            return self._reject(f"node_id inconnu {node_id[:40]!r}")
        if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_MQTT_PAYLOAD:
            return self._reject("payload absent ou trop gros")
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("objet JSON attendu")
            model = (TelemetryIn if kind == "telemetry" else StatusIn).model_validate(data)
        except (ValueError, ValidationError) as e:
            return self._reject(f"{kind} invalide de {node_id}: {str(e)[:120]}")

        if kind == "telemetry":
            await self._on_telemetry(node_id, model)
        else:
            await self._on_status(node_id, model)

    async def _on_telemetry(self, node_id: str, t: TelemetryIn) -> None:
        st = node_state(node_id)
        now = time.monotonic()
        # limite : 15 messages / 5 s par boîtier (tolère une rafale après un ralentissement)
        st.window = [x for x in st.window if now - x < 5.0]
        if len(st.window) >= 15:
            return self._reject(f"débit excessif de {node_id}")
        st.window.append(now)
        # anti-rejeu : seq doit progresser, sauf redémarrage (uptime qui repart de zéro)
        if st.last_seq is not None and st.last_uptime is not None:
            rebooted = t.uptime_ms < st.last_uptime
            if not rebooted and t.seq <= st.last_seq:
                return self._reject(f"seq rejouée {t.seq} <= {st.last_seq} ({node_id})")
        st.last_seq, st.last_uptime = t.seq, t.uptime_ms
        st.last_seen, st.online = now, True
        received_at = datetime.now(timezone.utc)      # horodatage serveur
        st.last_seen_at = received_at

        def save() -> None:
            with SessionLocal() as db:
                db.add(Telemetry(
                    node_id=node_id, seq=t.seq, uptime_ms=t.uptime_ms, temp=t.temp, hum=t.hum,
                    gas_raw=t.gas_raw, motion=t.motion, rssi=t.rssi, received_at=received_at,
                ))
                db.commit()
            counters.telemetry_saved += 1

        try:
            await asyncio.to_thread(save)
        except Exception:
            log.exception("Insertion télémétrie impossible")

        await hub.broadcast({
            "type": "telemetry",
            "node_id": node_id,
            "received_at": received_at.isoformat(),
            "data": t.model_dump(),
        })

        # Détection de mouvement : alerte sur front montant uniquement
        if t.motion and not st.last_motion and anomaly_cooldown_ok(f"{node_id}:motion"):
            await self._raise_alert(node_id, AlertType.MOTION, Severity.INFO, "pir", {"motion": 1.0})
            await self.beep(node_id, CommandAction.BEEP_2)     # signal sonore local (anti-spam : front montant + délai)
        st.last_motion = t.motion

        # --- Maintenance prédictive (Isolation Forest) ---
        if t.temp is not None and t.hum is not None and t.gas_raw is not None:
            result = await asyncio.to_thread(anomaly_model.predict, t.temp, t.hum, t.gas_raw)
            if result and result[0] and anomaly_cooldown_ok(node_id):
                metrics = {"temp": t.temp, "hum": t.hum, "gas_raw": float(t.gas_raw), "score": round(result[1], 4)}
                await self._raise_alert(node_id, AlertType.ANOMALY, Severity.CRITICAL, "isolation-forest", metrics)
                # réaction locale non dangereuse : 3 bips sur le boîtier
                await self.beep(node_id, CommandAction.BEEP_3)

    async def _raise_alert(self, node_id, alert_type, severity, source, metrics) -> None:
        try:
            out = await asyncio.to_thread(store_alert, node_id, alert_type, severity, source, metrics)
        except Exception:
            log.exception("Création d'alerte impossible")
            return
        await hub.broadcast({"type": "alert", "data": out.model_dump(mode="json")})

    async def _on_status(self, node_id: str, s: StatusIn) -> None:
        st = node_state(node_id)
        st.online = s.online
        if s.alarm is not None:
            st.alarm = s.alarm
        if s.online:
            st.last_seen = time.monotonic()
            st.last_seen_at = datetime.now(timezone.utc)
        if s.cmd_id is not None:
            st.last_cmd = {"id": s.cmd_id, "action": s.cmd.value if s.cmd else None, "ok": s.ok}
        await hub.broadcast({"type": "status", "data": node_summary(node_id)})

    async def beep(self, node_id: str, action: CommandAction) -> None:
        """Réaction sonore automatique décidée par le serveur (jamais par le navigateur)."""
        try:
            await self.send_command(node_id, action)
        except ConnectionError:
            log.warning("Bip impossible : broker MQTT indisponible")

    async def send_command(self, node_id: str, action: CommandAction) -> int:
        if not self.client or not self.connected:
            raise ConnectionError("Broker MQTT indisponible")
        self._cmd_id = (self._cmd_id + 1) % 2**31
        cmd_id = self._cmd_id
        await self.client.publish(
            f"sentinel/{node_id}/cmd",
            json.dumps({"id": cmd_id, "action": action.value}, separators=(",", ":")),
            qos=1,
        )
        counters.commands_sent += 1
        log.info("Commande %s #%d envoyée à %s", action.value, cmd_id, node_id)
        return cmd_id


mqtt_bridge = MqttBridge()
