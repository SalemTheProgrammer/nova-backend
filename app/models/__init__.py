"""ORM models for the manufacturing domain.

Importing this package registers every model on the shared Base metadata,
so `Base.metadata.create_all()` sees all tables.
"""
from __future__ import annotations

from app.models.enums import (
    StatutLot,
    StatutOF,
    TypeArticle,
    TypeMouvement,
    Unite,
)
from app.models.fabrication import OFConsommationMP, OrdreFabrication
from app.models.normes import Norme
from app.models.referentiel import (
    Article,
    Fournisseur,
    LigneProduction,
    MatierePremiere,
    Nomenclature,
    NomenclatureLigne,
)
from app.models.stock import LotMatierePremiere, MouvementStock

__all__ = [
    # enums
    "Unite",
    "TypeArticle",
    "StatutLot",
    "StatutOF",
    "TypeMouvement",
    # référentiel
    "Article",
    "MatierePremiere",
    "Fournisseur",
    "LigneProduction",
    "Nomenclature",
    "NomenclatureLigne",
    # stock
    "LotMatierePremiere",
    "MouvementStock",
    # fabrication
    "OrdreFabrication",
    "OFConsommationMP",
    # normes
    "Norme",
]
