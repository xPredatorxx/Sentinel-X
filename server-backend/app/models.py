from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# JSONB sous PostgreSQL, JSON générique ailleurs (tests sous SQLite)
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str | None] = mapped_column(String(16), unique=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    # heure serveur : seule horloge de confiance
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # horodatage annoncé par le client, conservé pour l'audit uniquement
    client_ts: Mapped[int | None] = mapped_column(BigInteger)
    alert_type: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(32), default="unknown")
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)


class Telemetry(Base):
    __tablename__ = "telemetry"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(BigInteger)
    uptime_ms: Mapped[int] = mapped_column(BigInteger)
    temp: Mapped[float | None] = mapped_column(Float)      # null si lecture capteur ratée
    hum: Mapped[float | None] = mapped_column(Float)
    gas_raw: Mapped[int | None] = mapped_column(Integer)
    motion: Mapped[bool | None] = mapped_column(Boolean)
    rssi: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_telemetry_node_time", "node_id", "received_at"),)
