"""Fabrication: ordres de fabrication et généalogie de consommation MP."""
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
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import StatutOF, Unite
from app.models.referentiel import Article, LigneProduction, MatierePremiere, Nomenclature
from app.models.stock import LotMatierePremiere


class OrdreFabrication(Base, TimestampMixin):
    """Ordre de fabrication (OF)."""

    __tablename__ = "ordre_fabrication"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    numero: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    article_id: Mapped[int] = mapped_column(ForeignKey("article.id"), index=True, nullable=False)
    nomenclature_id: Mapped[int] = mapped_column(
        ForeignKey("nomenclature.id"), nullable=False
    )
    quantite_planifiee: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unite: Mapped[Unite] = mapped_column(SAEnum(Unite), default=Unite.UN, nullable=False)
    ligne_production_id: Mapped[int | None] = mapped_column(
        ForeignKey("ligne_production.id"), nullable=True
    )
    statut: Mapped[StatutOF] = mapped_column(
        SAEnum(StatutOF), default=StatutOF.BROUILLON, nullable=False
    )
    numero_lot_produit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Échéance client : ENTRÉE de l'ordonnancement. Les règles EDD / ratio
    # critique / marge trient dessus et le retard se mesure contre elle, donc
    # l'ordonnanceur ne l'écrit JAMAIS — seul l'opérateur la fixe.
    date_echeance: Mapped[date | None] = mapped_column(Date, nullable=True)
    # SORTIES de l'ordonnanceur : le créneau projeté par la règle de dispatching.
    # Écrites uniquement par `planning_service.appliquer`, sur les OF PLANIFIE.
    date_debut_prevue: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    date_fin_prevue: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    date_debut_reelle: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    date_fin_reelle: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cree_par: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Suivi d'exécution (alimenté par la télémétrie des automates, pas la planification).
    quantite_bonne: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    quantite_rejetee: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )

    article: Mapped[Article] = relationship()
    nomenclature: Mapped[Nomenclature] = relationship()
    ligne_production: Mapped[LigneProduction | None] = relationship()
    consommations: Mapped[list["OFConsommationMP"]] = relationship(
        back_populates="ordre_fabrication", cascade="all, delete-orphan"
    )


class OFConsommationMP(Base):
    """Généalogie: quels lots MP (et quelles quantités) ont nourri cet OF."""

    __tablename__ = "of_consommation_mp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ordre_fabrication_id: Mapped[int] = mapped_column(
        ForeignKey("ordre_fabrication.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lot_matiere_premiere_id: Mapped[int] = mapped_column(
        ForeignKey("lot_matiere_premiere.id"), nullable=False
    )
    matiere_premiere_id: Mapped[int] = mapped_column(
        ForeignKey("matiere_premiere.id"), index=True, nullable=False
    )
    quantite_consommee: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    date_consommation: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    ordre_fabrication: Mapped[OrdreFabrication] = relationship(back_populates="consommations")
    lot: Mapped[LotMatierePremiere] = relationship()
    matiere_premiere: Mapped[MatierePremiere] = relationship()
