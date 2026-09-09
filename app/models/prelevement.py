"""Prélèvement de matières premières (BPF / DPM Tunisie & normes pharma/agro)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import Unite
from app.models.stock import LotMatierePremiere


class StatutPrelevement(str, Enum):
    """Statut du prélèvement et de l'analyse contrôle qualité."""

    EN_ATTENTE_CQ = "EN_ATTENTE_CQ"  # Échantillon prélevé, analyse en cours
    CONFORME = "CONFORME"            # Conforme au bulletin d'analyse (lot libéré)
    NON_CONFORME = "NON_CONFORME"    # Non conforme (lot rejeté / bloqué)


class PrelevementMP(Base, TimestampMixin):
    """Fiche de prélèvement d'échantillons sur un lot de matière première."""

    __tablename__ = "prelevement_mp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    numero: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    lot_matiere_premiere_id: Mapped[int] = mapped_column(
        ForeignKey("lot_matiere_premiere.id"), index=True, nullable=False
    )
    quantite_prelevee: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unite: Mapped[Unite] = mapped_column(SAEnum(Unite), default=Unite.G, nullable=False)
    preleveur: Mapped[str] = mapped_column(String(100), nullable=False)
    zone_prelevement: Mapped[str] = mapped_column(
        String(150), default="SAS Prélèvement MP - Flux Laminaire ISO 5", nullable=False
    )
    date_prelevement: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    statut: Mapped[StatutPrelevement] = mapped_column(
        SAEnum(StatutPrelevement), default=StatutPrelevement.EN_ATTENTE_CQ, nullable=False
    )
    date_analyse: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    analyste: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bulletin_analyse_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    commentaire: Mapped[str | None] = mapped_column(String(255), nullable=True)

    lot: Mapped[LotMatierePremiere] = relationship()
