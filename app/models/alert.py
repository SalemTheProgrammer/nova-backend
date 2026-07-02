"""Alert: alertes générées par le système à partir des événements machine."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import SeveriteAlerte
from app.models.fabrication import OrdreFabrication
from app.models.machine import Machine


class Alert(Base):
    """Une alerte (arrêt, alarme, dérive qualité) affichée au tableau de bord."""

    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machine.id"), nullable=True)
    ordre_fabrication_id: Mapped[int | None] = mapped_column(
        ForeignKey("ordre_fabrication.id"), nullable=True
    )
    severity: Mapped[SeveriteAlerte] = mapped_column(SAEnum(SeveriteAlerte), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    machine: Mapped[Machine | None] = relationship()
    ordre_fabrication: Mapped[OrdreFabrication | None] = relationship()
