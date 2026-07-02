"""Machine: état SCADA courant d'une machine simulée."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import StatutMachine
from app.models.fabrication import OrdreFabrication
from app.models.referentiel import LigneProduction


class Machine(Base, TimestampMixin):
    """Machine physique (simulée) rattachée à une ligne de production."""

    __tablename__ = "machine"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    ligne_production_id: Mapped[int] = mapped_column(
        ForeignKey("ligne_production.id"), index=True, nullable=False
    )
    ordre_fabrication_id: Mapped[int | None] = mapped_column(
        ForeignKey("ordre_fabrication.id"), nullable=True
    )
    statut: Mapped[StatutMachine] = mapped_column(
        SAEnum(StatutMachine), default=StatutMachine.ARRET, nullable=False
    )
    temps_cycle_cible_s: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    temps_cycle_actuel_s: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    quantite_produite: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quantite_bonne: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quantite_rejetee: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dernier_evenement_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    ligne_production: Mapped[LigneProduction] = relationship()
    ordre_fabrication: Mapped[OrdreFabrication | None] = relationship()
