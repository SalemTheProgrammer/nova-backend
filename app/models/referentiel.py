"""Master data (référentiel): articles, matières premières, fournisseurs, lignes, nomenclatures."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import TypeArticle, Unite

# Association ligne <-> articles : quels articles (produits) une ligne sait produire.
ligne_article = Table(
    "ligne_article",
    Base.metadata,
    Column(
        "ligne_production_id",
        ForeignKey("ligne_production.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("article_id", ForeignKey("article.id", ondelete="CASCADE"), primary_key=True),
)


class Article(Base, TimestampMixin):
    """Produit fabriqué (produit fini)."""

    __tablename__ = "article"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    unite: Mapped[Unite] = mapped_column(SAEnum(Unite), default=Unite.UN, nullable=False)
    type: Mapped[TypeArticle] = mapped_column(
        SAEnum(TypeArticle), default=TypeArticle.PF, nullable=False
    )
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Temps de cycle standard (secondes/unité), utilisé pour le calcul du TRS (TN, TP).
    temps_cycle_cible_s: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Valeur commerciale d'une unité produite (TND) : sert à chiffrer en dinars
    # la production perdue lors d'un arrêt (what-if, superviseur, rapports).
    valeur_unitaire: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)

    nomenclatures: Mapped[list["Nomenclature"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class MatierePremiere(Base, TimestampMixin):
    """Matière première (référence)."""

    __tablename__ = "matiere_premiere"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    unite: Mapped[Unite] = mapped_column(SAEnum(Unite), default=Unite.KG, nullable=False)
    seuil_alerte: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Prix d'achat unitaire (TND / unité de `unite`) : sert à chiffrer le coût
    # matières d'un OF à partir de sa généalogie de consommation (FEFO).
    prix_unitaire_tnd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    lots: Mapped[list["LotMatierePremiere"]] = relationship(  # noqa: F821
        back_populates="matiere_premiere"
    )


class Fournisseur(Base, TimestampMixin):
    """Fournisseur de matières premières."""

    __tablename__ = "fournisseur"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LigneProduction(Base, TimestampMixin):
    """Ligne de production."""

    __tablename__ = "ligne_production"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Multiplicateurs simples pour TRG/TRE (AFNOR NF E60-182), pas de calendrier d'équipes v1.
    taux_charge: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1.0"), nullable=False)
    taux_engagement: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=Decimal("1.0"), nullable=False
    )

    # Articles que cette ligne sait produire.
    articles: Mapped[list["Article"]] = relationship(secondary=ligne_article)


class LigneLien(Base, TimestampMixin):
    """Lien de flux entre deux lignes : la sortie de `source` alimente `target`.

    Modélise l'atelier comme un graphe (façon n8n) : une ligne peut être l'entrée
    d'une autre (produit semi-fini -> ligne suivante).
    """

    __tablename__ = "ligne_lien"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", name="uq_ligne_lien_source_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("ligne_production.id", ondelete="CASCADE"), index=True, nullable=False
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("ligne_production.id", ondelete="CASCADE"), index=True, nullable=False
    )


class Nomenclature(Base, TimestampMixin):
    """En-tête de nomenclature (formule), versionnée par article."""

    __tablename__ = "nomenclature"
    __table_args__ = (
        UniqueConstraint("article_id", "version", name="uq_nomenclature_article_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("article.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    article: Mapped[Article] = relationship(back_populates="nomenclatures")
    lignes: Mapped[list["NomenclatureLigne"]] = relationship(
        back_populates="nomenclature", cascade="all, delete-orphan"
    )


class NomenclatureLigne(Base):
    """Ligne de nomenclature: une MP et sa quantité par unité d'article produit."""

    __tablename__ = "nomenclature_ligne"
    __table_args__ = (
        UniqueConstraint(
            "nomenclature_id", "matiere_premiere_id", name="uq_nomligne_nom_mp"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nomenclature_id: Mapped[int] = mapped_column(
        ForeignKey("nomenclature.id", ondelete="CASCADE"), index=True, nullable=False
    )
    matiere_premiere_id: Mapped[int] = mapped_column(
        ForeignKey("matiere_premiere.id"), index=True, nullable=False
    )
    quantite_par_unite: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    nomenclature: Mapped[Nomenclature] = relationship(back_populates="lignes")
    matiere_premiere: Mapped[MatierePremiere] = relationship()
