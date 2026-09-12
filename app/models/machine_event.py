"""MachineEvent : journal append-only de tous les événements machine."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import TypeEvenementMachine
from app.models.fabrication import OrdreFabrication
from app.models.machine import Machine


class MachineEvent(Base):
    """Un événement machine (source de vérité pour l'état et le TRS), traduit de
    la télémétrie de l'automate — `payload.source` indique l'origine."""

    __tablename__ = "machine_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ordre_fabrication_id: Mapped[int | None] = mapped_column(
        ForeignKey("ordre_fabrication.id"), nullable=True
    )
    type: Mapped[TypeEvenementMachine] = mapped_column(
        SAEnum(TypeEvenementMachine), index=True, nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )

    machine: Mapped[Machine] = relationship()
    ordre_fabrication: Mapped[OrdreFabrication | None] = relationship()
