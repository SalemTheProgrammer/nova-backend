"""MaintenanceEvent: interventions de maintenance sur une machine."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import TypeMaintenance
from app.models.machine import Machine


class MaintenanceEvent(Base):
    """Une intervention de maintenance (préventive, corrective, urgence)."""

    __tablename__ = "maintenance_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[TypeMaintenance] = mapped_column(SAEnum(TypeMaintenance), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    prochaine_maintenance: Mapped[date | None] = mapped_column(Date, nullable=True)

    machine: Mapped[Machine] = relationship()
