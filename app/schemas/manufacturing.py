"""Pydantic schemas for the manufacturing REST API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StatutLot, StatutOF, TypeArticle, TypeMouvement, Unite


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------- Fournisseur --------------------------- #
class FournisseurBase(BaseModel):
    code: str
    nom: str
    contact: str | None = None


class FournisseurCreate(FournisseurBase):
    pass


class FournisseurUpdate(BaseModel):
    code: str | None = None
    nom: str | None = None
    contact: str | None = None


class FournisseurRead(_ORM, FournisseurBase):
    id: int


# --------------------------- Article --------------------------- #
class ArticleBase(BaseModel):
    code: str
    designation: str
    unite: Unite = Unite.UN
    type: TypeArticle = TypeArticle.PF
    actif: bool = True


class ArticleCreate(ArticleBase):
    pass


class ArticleUpdate(BaseModel):
    code: str | None = None
    designation: str | None = None
    unite: Unite | None = None
    type: TypeArticle | None = None
    actif: bool | None = None


class ArticleRead(_ORM, ArticleBase):
    id: int


# --------------------------- Nomenclature --------------------------- #
class NomenclatureLigneIn(BaseModel):
    matiere_premiere_id: int
    quantite_par_unite: Decimal = Field(gt=0)


class NomenclatureLigneRead(BaseModel):
    id: int
    matiere_premiere_id: int
    code_mp: str
    designation_mp: str
    unite: Unite
    quantite_par_unite: Decimal


class NomenclatureRead(BaseModel):
    id: int
    article_id: int
    version: int
    actif: bool
    lignes: list[NomenclatureLigneRead]


class NomenclatureCreate(BaseModel):
    """Crée une nouvelle version de nomenclature (formule) pour l'article."""

    lignes: list[NomenclatureLigneIn] = Field(min_length=1)


# --------------------------- Matière première --------------------------- #
class MatierePremiereBase(BaseModel):
    code: str
    designation: str
    unite: Unite = Unite.KG
    seuil_alerte: Decimal | None = None
    actif: bool = True


class MatierePremiereCreate(MatierePremiereBase):
    pass


class MatierePremiereUpdate(BaseModel):
    code: str | None = None
    designation: str | None = None
    unite: Unite | None = None
    seuil_alerte: Decimal | None = None
    actif: bool | None = None


class MatierePremiereRead(_ORM, MatierePremiereBase):
    id: int
    stock_disponible: Decimal | None = None


# --------------------------- Lot MP --------------------------- #
class LotCreate(BaseModel):
    """Réception d'un lot de matière première."""

    numero_lot: str
    quantite: Decimal = Field(gt=0)
    date_reception: date | None = None
    date_peremption: date | None = None
    fournisseur_id: int | None = None


class LotRead(_ORM):
    id: int
    numero_lot: str
    matiere_premiere_id: int
    fournisseur_id: int | None
    quantite_initiale: Decimal
    quantite_restante: Decimal
    date_reception: date
    date_peremption: date | None
    statut: StatutLot


# --------------------------- Stock (CRUD lots + mouvements) --------------------------- #
class StockLotCreate(BaseModel):
    """Réception d'un lot en stock pour une matière première donnée."""

    matiere_premiere_id: int
    numero_lot: str
    quantite: Decimal = Field(gt=0)
    date_reception: date | None = None
    date_peremption: date | None = None
    fournisseur_id: int | None = None


class LotUpdate(BaseModel):
    """Mise à jour des métadonnées d'un lot (hors quantité — voir l'ajustement)."""

    statut: StatutLot | None = None
    date_peremption: date | None = None
    fournisseur_id: int | None = None


class AjustementCreate(BaseModel):
    """Ajustement d'inventaire : fixe la quantité restante et journalise l'écart."""

    quantite_restante: Decimal = Field(ge=0)
    commentaire: str | None = None


class LotDetailRead(LotRead):
    """Lot enrichi du code/désignation/unité de sa matière première."""

    code_mp: str
    designation_mp: str
    unite: Unite


class MouvementRead(_ORM):
    id: int
    type_mouvement: TypeMouvement
    matiere_premiere_id: int
    lot_matiere_premiere_id: int | None
    quantite: Decimal
    reference_type: str | None
    reference_id: int | None
    commentaire: str | None
    date_mouvement: datetime


# --------------------------- Ligne de production --------------------------- #
class LigneProductionBase(BaseModel):
    code: str
    designation: str
    actif: bool = True


class LigneProductionCreate(LigneProductionBase):
    pass


class LigneProductionUpdate(BaseModel):
    code: str | None = None
    designation: str | None = None
    actif: bool | None = None


class LigneProductionRead(_ORM, LigneProductionBase):
    id: int


# --------------------------- Faisabilité --------------------------- #
class BesoinRead(BaseModel):
    matiere_premiere_id: int
    code: str
    designation: str
    unite: str
    quantite_requise: Decimal
    quantite_disponible: Decimal
    manquant: Decimal
    suffisant: bool


class FaisabiliteRead(BaseModel):
    article_id: int
    quantite: Decimal
    faisable: bool
    besoins: list[BesoinRead]


# --------------------------- Ordre de fabrication --------------------------- #
class OFCreate(BaseModel):
    article_id: int
    quantite: Decimal = Field(gt=0)
    date_fin_prevue: date | None = None
    ligne_production_id: int | None = None
    cree_par: str | None = None


class OFStatutUpdate(BaseModel):
    statut: StatutOF


class OFConsommationRead(BaseModel):
    matiere_premiere_id: int
    code_mp: str
    lot_id: int
    numero_lot: str
    quantite_consommee: Decimal


class OFRead(BaseModel):
    id: int
    numero: str
    article_id: int
    code_article: str
    designation_article: str
    quantite_planifiee: Decimal
    unite: Unite
    statut: StatutOF
    numero_lot_produit: str | None
    date_fin_prevue: date | None
    ligne_production_id: int | None
    date_creation: datetime
    date_debut_reelle: datetime | None = None
    date_fin_reelle: datetime | None = None
    quantite_bonne: Decimal = Decimal("0")
    quantite_rejetee: Decimal = Decimal("0")
    consommations: list[OFConsommationRead] = []


# --------------------------- Stock overview --------------------------- #
class StockMPRead(BaseModel):
    matiere_premiere_id: int
    code: str
    designation: str
    unite: Unite
    disponible: Decimal
    seuil_alerte: Decimal | None
    sous_seuil: bool
    nb_lots: int
