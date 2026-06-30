"""Stock & traçabilité: lots de matière première, mouvements de stock."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import StatutLot, TypeMouvement
from app.models.referentiel import Fournisseur, MatierePremiere


class LotMatierePremiere(Base, TimestampMixin):
    """Lot de matière première en stock (traçabilité + FEFO)."""

    __tablename__ = "lot_matiere_premiere"
    __table_args__ = (
        UniqueConstraint(
            "matiere_premiere_id", "numero_lot", name="uq_lotmp_mp_numero"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    numero_lot: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    matiere_premiere_id: Mapped[int] = mapped_column(
        ForeignKey("matiere_premiere.id"), index=True, nullable=False
    )
    fournisseur_id: Mapped[int | None] = mapped_column(
        ForeignKey("fournisseur.id"), nullable=True
    )
    quantite_initiale: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    quantite_restante: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    date_reception: Mapped[date] = mapped_column(Date, nullable=False)
    # Index for FEFO ordering (oldest expiry first).
    date_peremption: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    statut: Mapped[StatutLot] = mapped_column(
        SAEnum(StatutLot), default=StatutLot.DISPONIBLE, nullable=False
    )

    matiere_premiere: Mapped[MatierePremiere] = relationship(back_populates="lots")
    fournisseur: Mapped[Fournisseur | None] = relationship()


class MouvementStock(Base):
    """Journal d'audit de tout mouvement de stock (entrée, sortie, ajustement)."""

    __tablename__ = "mouvement_stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_mouvement: Mapped[TypeMouvement] = mapped_column(
        SAEnum(TypeMouvement), nullable=False
    )
    matiere_premiere_id: Mapped[int] = mapped_column(
        ForeignKey("matiere_premiere.id"), index=True, nullable=False
    )
    lot_matiere_premiere_id: Mapped[int | None] = mapped_column(
        ForeignKey("lot_matiere_premiere.id"), nullable=True
    )
    quantite: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commentaire: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_mouvement: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    matiere_premiere: Mapped[MatierePremiere] = relationship()
    lot: Mapped[LotMatierePremiere | None] = relationship()
