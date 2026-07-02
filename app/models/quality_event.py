"""QualityEvent: pièces bonnes / rebuts produits par une machine."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import CauseRebut, TypeEvenementQualite
from app.models.fabrication import OrdreFabrication
from app.models.machine import Machine


class QualityEvent(Base):
    """Un événement qualité : une unité bonne, ou un rebut avec sa cause."""

    __tablename__ = "quality_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ordre_fabrication_id: Mapped[int | None] = mapped_column(
        ForeignKey("ordre_fabrication.id"), nullable=True
    )
    type: Mapped[TypeEvenementQualite] = mapped_column(
        SAEnum(TypeEvenementQualite), nullable=False
    )
    quantite: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    cause: Mapped[CauseRebut | None] = mapped_column(SAEnum(CauseRebut), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )

    machine: Mapped[Machine] = relationship()
    ordre_fabrication: Mapped[OrdreFabrication | None] = relationship()
