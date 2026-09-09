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
    StatutProposition,
    TypeArticle,
    TypeEvenementMachine,
    TypeEvenementQualite,
    TypeMaintenance,
    TypeMouvement,
    Unite,
)
from app.models.agent_proposal import AgentProposal
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.envoi_planifie import EnvoiPlanifie
from app.models.downtime_event import DowntimeEvent
from app.models.fabrication import OFConsommationMP, OrdreFabrication
from app.models.machine import Machine
from app.models.machine_event import MachineEvent
from app.models.maintenance_event import MaintenanceEvent
from app.models.documents import DocumentRag
from app.models.quality_event import QualityEvent
from app.models.referentiel import (
    Article,
    Fournisseur,
    LigneLien,
    LigneProduction,
    MatierePremiere,
    Nomenclature,
    NomenclatureLigne,
    ligne_article,
)
from app.models.stock import LotMatierePremiere, MouvementStock
from app.models.prelevement import PrelevementMP, StatutPrelevement
from app.models.sparkplug import (
    SparkplugDevice,
    SparkplugTagMapping,
    TagTransformation,
    TargetKpi,
)
from app.models.utilisateur import Utilisateur
from app.models.code_verification import CodeVerification

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
    "LigneLien",
    "ligne_article",
    # stock
    "LotMatierePremiere",
    "MouvementStock",
    # fabrication
    "OrdreFabrication",
    "OFConsommationMP",
    # base documentaire (RAG)
    "DocumentRag",
    # MES / SCADA
    "Machine",
    "MachineEvent",
    "DowntimeEvent",
    "QualityEvent",
    "MaintenanceEvent",
    "Alert",
    # superviseur autonome
    "StatutProposition",
    "AgentProposal",
    # envois sortants programmés
    "EnvoiPlanifie",
    # journal d'audit (BPF / Annexe 11)
    "AuditLog",
    # authentification par numéro + périmètre d'outils
    "Utilisateur",
    "CodeVerification",
    # prélèvement MP (BPF / DPM)
    "PrelevementMP",
    "StatutPrelevement",
    # Sparkplug B & IoT
    "SparkplugDevice",
    "SparkplugTagMapping",
    "TargetKpi",
    "TagTransformation",
]
