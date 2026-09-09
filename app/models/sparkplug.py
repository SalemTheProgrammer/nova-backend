"""Configuration Sparkplug B & Mappage des Tags vers les KPIs MES."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.machine import Machine


class TargetKpi(str, Enum):
    """Indicateurs cibles du MES pouvant être alimentés par Sparkplug B."""

    BONNES_PIECES = "BONNES_PIECES"
    REJETS = "REJETS"
    CADENCE = "CADENCE"
    STATUT_MACHINE = "STATUT_MACHINE"
    TEMPERATURE = "TEMPERATURE"
    PUISSANCE = "PUISSANCE"
    ARRET_DETECTE = "ARRET_DETECTE"
    OPERATOR_CARD = "OPERATOR_CARD"  # Badge / carte pour qualifier les arrêts


class TagTransformation(str, Enum):
    """Règle de conversion ou calcul d'un tag MQTT."""

    DIRECT = "DIRECT"              # Valeur brute
    SCALE_FACTOR = "SCALE_FACTOR"  # Multiplication / facteur d'échelle
    DELTA_RATE = "DELTA_RATE"      # Dérivée par minute (cadence)
    THRESHOLD_STATE = "THRESHOLD_STATE"  # Seuil -> État (ex: val > 0 -> MARCHE)
    OPERATOR_CARD = "OPERATOR_CARD"      # Identification de carte d'arrêt/opérateur


class SparkplugDevice(Base, TimestampMixin):
    """Équipement industriel Sparkplug B connecté via MQTT."""

    __tablename__ = "sparkplug_device"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    group_id: Mapped[str] = mapped_column(String(50), default="NovaMES", nullable=False)
    edge_node_id: Mapped[str] = mapped_column(String(50), default="Ligne1_Edge", nullable=False)
    device_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    machine_id: Mapped[int | None] = mapped_column(
        ForeignKey("machine.id"), nullable=True
    )
    broker_url: Mapped[str] = mapped_column(String(255), default="internal", nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_birth_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_data_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    available_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    machine: Mapped[Machine | None] = relationship()
    mappings: Mapped[list["SparkplugTagMapping"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class SparkplugTagMapping(Base, TimestampMixin):
    """Règle de mappage personnalisée choisie par l'utilisateur."""

    __tablename__ = "sparkplug_tag_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("sparkplug_device.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_kpi: Mapped[TargetKpi] = mapped_column(SAEnum(TargetKpi), nullable=False)
    transformation: Mapped[TagTransformation] = mapped_column(
        SAEnum(TagTransformation), default=TagTransformation.DIRECT, nullable=False
    )
    formula_param: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    device: Mapped[SparkplugDevice] = relationship(back_populates="mappings")
