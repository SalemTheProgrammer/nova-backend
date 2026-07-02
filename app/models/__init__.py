"""ORM models for the manufacturing domain.

Importing this package registers every model on the shared Base metadata,
so `Base.metadata.create_all()` sees all tables.
"""
from __future__ import annotations

from app.models.enums import (
    CauseArret,
    CauseRebut,
    SeveriteAlerte,
    StatutLot,
    StatutMachine,
    StatutOF,
    TypeArticle,
    TypeEvenementMachine,
    TypeEvenementQualite,
    TypeMaintenance,
    TypeMouvement,
    Unite,
)
from app.models.alert import Alert
from app.models.downtime_event import DowntimeEvent
from app.models.fabrication import OFConsommationMP, OrdreFabrication
from app.models.machine import Machine
from app.models.machine_event import MachineEvent
from app.models.maintenance_event import MaintenanceEvent
from app.models.normes import Norme
from app.models.quality_event import QualityEvent
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
    "StatutMachine",
    "TypeEvenementMachine",
    "CauseArret",
    "CauseRebut",
    "TypeMaintenance",
    "SeveriteAlerte",
    "TypeEvenementQualite",
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
    # MES / SCADA
    "Machine",
    "MachineEvent",
    "DowntimeEvent",
    "QualityEvent",
    "MaintenanceEvent",
    "Alert",
]
