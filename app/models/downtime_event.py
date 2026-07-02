"""DowntimeEvent: arrêts machine (actifs et historique)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import CauseArret
from app.models.fabrication import OrdreFabrication
from app.models.machine import Machine


class DowntimeEvent(Base):
    """Un arrêt machine : début, fin (nullable = en cours), cause, commentaire."""

    __tablename__ = "downtime_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ordre_fabrication_id: Mapped[int | None] = mapped_column(
        ForeignKey("ordre_fabrication.id"), nullable=True
    )
    cause: Mapped[CauseArret] = mapped_column(SAEnum(CauseArret), nullable=False)
    operator_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    machine: Mapped[Machine] = relationship()
    ordre_fabrication: Mapped[OrdreFabrication | None] = relationship()
