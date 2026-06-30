"""Master data (référentiel): articles, matières premières, fournisseurs, lignes, nomenclatures."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import TypeArticle, Unite


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
