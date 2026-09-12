"""Équipements Sparkplug B (automates) et règles de mappage tags → KPI MES."""
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
    """Indicateur MES alimenté par un tag d'automate."""

    BONNES_PIECES = "BONNES_PIECES"  # total monotone de pièces bonnes
    REJETS = "REJETS"  # total monotone de rebuts
    CADENCE = "CADENCE"  # unités / minute
    STATUT_MACHINE = "STATUT_MACHINE"  # état (valeurs de StatutMachine)
    CAUSE_ARRET = "CAUSE_ARRET"  # cause d'arrêt (valeurs de CauseArret), lue avec l'état PANNE
    CAUSE_REBUT = "CAUSE_REBUT"  # cause de rebut (valeurs de CauseRebut), lue avec les rejets
    TEMPERATURE = "TEMPERATURE"  # °C
    PUISSANCE = "PUISSANCE"  # kW
    OPERATOR_CARD = "OPERATOR_CARD"  # badge opérateur qualifiant un arrêt


class TagTransformation(str, Enum):
    """Conversion appliquée à la valeur brute d'un tag avant son usage MES."""

    DIRECT = "DIRECT"  # valeur brute
    SCALE_FACTOR = "SCALE_FACTOR"  # valeur × formula_param
    THRESHOLD_STATE = "THRESHOLD_STATE"  # valeur > formula_param → MARCHE, sinon ARRET
    OPERATOR_CARD = "OPERATOR_CARD"  # code badge normalisé (majuscules)


class SparkplugDevice(Base, TimestampMixin):
    """Automate (device Sparkplug B) vu par l'hôte Nova sur le broker MQTT.

    Identité : `device_id`, unique dans le MES. Convention : `device_id` = code
    de la machine pilotée, ce qui permet le rattachement automatique à la
    première naissance (modifiable ensuite depuis le studio Sparkplug).
    """

    __tablename__ = "sparkplug_device"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    group_id: Mapped[str] = mapped_column(String(100), nullable=False)
    edge_node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    device_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machine.id"), nullable=True)
    broker_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_birth_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_data_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Catalogue des métriques déclarées à la dernière naissance (nom → type, valeur).
    available_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Dernière valeur reçue par métrique : référence des compteurs (deltas) et
    # détection de changement. Persistée pour survivre aux redémarrages du MES.
    last_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    machine: Mapped[Machine | None] = relationship()
    mappings: Mapped[list["SparkplugTagMapping"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class SparkplugTagMapping(Base, TimestampMixin):
    """Règle : tel tag de l'automate alimente tel KPI MES (après transformation)."""

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
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    device: Mapped[SparkplugDevice] = relationship(back_populates="mappings")
