from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Identifiant de boîtier : minuscules, chiffres et tirets, 3 à 32 caractères.
NodeId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")]
# Clé de métrique : identifiant court, pas de caractères spéciaux.
MetricKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]


class StrictModel(BaseModel):
    # extra="forbid" : un champ inconnu est refusé (422) au lieu d'être ignoré.
    # allow_inf_nan=False : NaN / Infinity sont refusés dans les nombres.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertType(str, Enum):
    GAS_LEAK = "GAS_LEAK"      # IA prédictive (anomalie portée par le gaz)
    OVERHEAT = "OVERHEAT"      # IA prédictive (anomalie portée par la température)
    MOTION = "MOTION"          # capteur PIR
    INTRUDER = "INTRUDER"      # IA vision (webcam)
    ANOMALY = "ANOMALY"        # IA prédictive (Isolation Forest)


class AlertIn(StrictModel):
    node_id: NodeId = Field(examples=["sentinel-edge-01"])
    # facultatif : l'heure de référence reste celle du serveur (received_at)
    timestamp: int | None = Field(default=None, ge=0, examples=[1791200000])
    alert_type: AlertType
    severity: Severity
    metrics: dict[MetricKey, float] = Field(
        default_factory=dict, max_length=16, examples=[{"confidence": 0.91}]
    )


class AlertOut(BaseModel):
    incident_id: str
    node_id: str
    timestamp: datetime        # = received_at côté base (heure serveur)
    alert_type: AlertType
    severity: Severity
    source: str
    metrics: dict[str, float]


class AlertAck(BaseModel):
    status: str = "ACK"
    incident_id: str


# --- Télémétrie (MQTT) ------------------------------------------------------

class TelemetryIn(StrictModel):
    """Payload publié par l'ESP sur sentinel/<node_id>/telemetry.

    Pas de node_id ni d'horodatage dans le message : le node_id vient du topic
    (verrouillé par l'ACL du broker) et l'heure est ajoutée par le serveur.
    """
    seq: int = Field(ge=0, le=2**40)
    uptime_ms: int = Field(ge=0, le=2**40)
    temp: float | None = Field(default=None, ge=-40, le=85)      # plage du DHT22
    hum: float | None = Field(default=None, ge=0, le=100)
    gas_raw: int | None = Field(default=None, ge=0, le=4095)     # ADC 12 bits
    gas_v: float | None = Field(default=None, ge=0, le=5)
    motion: bool = False
    rssi: int | None = Field(default=None, ge=-120, le=0)


class CommandAction(str, Enum):
    ALARM_ON = "ALARM_ON"
    ALARM_OFF = "ALARM_OFF"
    BEEP_2 = "BEEP_2"
    BEEP_3 = "BEEP_3"
    LED_ON = "LED_ON"
    LED_OFF = "LED_OFF"


class StatusIn(StrictModel):
    """Payload publié (retenu) par l'ESP sur sentinel/<node_id>/status."""
    online: bool
    cmd_id: int | None = Field(default=None, ge=0, le=2**31)
    cmd: CommandAction | None = None
    ok: bool | None = None
    alarm: bool | None = None


class TelemetryOut(BaseModel):
    node_id: str
    received_at: datetime
    seq: int
    uptime_ms: int
    temp: float | None
    hum: float | None
    gas_raw: int | None
    motion: bool | None
    rssi: int | None


class HistoryRange(str, Enum):
    m15 = "15m"
    h1 = "1h"
    h6 = "6h"
    h24 = "24h"


# --- Commandes --------------------------------------------------------------

class ActuatorCommand(StrictModel):
    node_id: NodeId
    action: CommandAction


class CommandAccepted(BaseModel):
    status: str = "ACCEPTED"
    command_id: int
    node_id: str
    action: CommandAction


# --- Authentification ---------------------------------------------------------

class LoginIn(StrictModel):
    username: Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class UserOut(BaseModel):
    username: str
    role: str
