"""Enumerations for the manufacturing domain (French/pharma norms)."""
from __future__ import annotations

from enum import Enum


class Unite(str, Enum):
    """Unités de mesure."""

    KG = "KG"
    G = "G"
    L = "L"
    ML = "ML"
    UN = "UN"  # unité / pièce


class TypeArticle(str, Enum):
    """Type d'article."""

    PF = "PF"  # produit fini
    PSF = "PSF"  # produit semi-fini (réservé, BOM multi-niveau futur)


class StatutLot(str, Enum):
    """Statut qualité/disponibilité d'un lot de matière première."""

    DISPONIBLE = "DISPONIBLE"
    BLOQUE = "BLOQUE"  # quarantaine / contrôle qualité
    PERIME = "PERIME"
    EPUISE = "EPUISE"


class StatutOF(str, Enum):
    """Cycle de vie d'un ordre de fabrication."""

    BROUILLON = "BROUILLON"
    PLANIFIE = "PLANIFIE"
    EN_COURS = "EN_COURS"
    TERMINE = "TERMINE"
    ANNULE = "ANNULE"


class TypeMouvement(str, Enum):
    """Type de mouvement de stock."""

    ENTREE = "ENTREE"  # réception
    SORTIE = "SORTIE"  # consommation OF
    AJUSTEMENT = "AJUSTEMENT"  # inventaire / correction


class StatutMachine(str, Enum):
    """État courant d'une machine (SCADA)."""

    ARRET = "ARRET"
    MARCHE = "MARCHE"
    PAUSE = "PAUSE"
    PANNE = "PANNE"
    MAINTENANCE = "MAINTENANCE"


class TypeEvenementMachine(str, Enum):
    """Type d'événement émis par le simulateur machine."""

    MACHINE_STARTED = "MACHINE_STARTED"
    MACHINE_STOPPED = "MACHINE_STOPPED"
    MACHINE_IDLE = "MACHINE_IDLE"
    MACHINE_ALARM = "MACHINE_ALARM"
    MACHINE_MAINTENANCE = "MACHINE_MAINTENANCE"
    PRODUCTION_COUNT_UPDATED = "PRODUCTION_COUNT_UPDATED"
    GOOD_UNIT_PRODUCED = "GOOD_UNIT_PRODUCED"
    SCRAP_UNIT_PRODUCED = "SCRAP_UNIT_PRODUCED"
    CYCLE_TIME_CHANGED = "CYCLE_TIME_CHANGED"
    DOWNTIME_STARTED = "DOWNTIME_STARTED"
    DOWNTIME_RESOLVED = "DOWNTIME_RESOLVED"
    QUALITY_EVENT_CREATED = "QUALITY_EVENT_CREATED"
    MAINTENANCE_STARTED = "MAINTENANCE_STARTED"
    MAINTENANCE_ENDED = "MAINTENANCE_ENDED"
    SENSOR_TAG_UPDATED = "SENSOR_TAG_UPDATED"


class CauseArret(str, Enum):
    """Causes d'arrêt (downtime)."""

    PANNE_MECANIQUE = "PANNE_MECANIQUE"
    PANNE_ELECTRIQUE = "PANNE_ELECTRIQUE"
    ATTENTE_MATIERE = "ATTENTE_MATIERE"
    CHANGEMENT_SERIE = "CHANGEMENT_SERIE"
    REGLAGE_MACHINE = "REGLAGE_MACHINE"
    MANQUE_OPERATEUR = "MANQUE_OPERATEUR"
    NETTOYAGE = "NETTOYAGE"
    MAINTENANCE_PLANIFIEE = "MAINTENANCE_PLANIFIEE"
    MICRO_ARRET = "MICRO_ARRET"
    QUALITE_BLOQUANTE = "QUALITE_BLOQUANTE"
    AUTRE = "AUTRE"


class CauseRebut(str, Enum):
    """Causes de rebut (scrap)."""

    DEFAUT_MATIERE = "DEFAUT_MATIERE"
    DEFAUT_DIMENSIONNEL = "DEFAUT_DIMENSIONNEL"
    DEFAUT_VISUEL = "DEFAUT_VISUEL"
    MAUVAIS_REGLAGE = "MAUVAIS_REGLAGE"
    ERREUR_OPERATEUR = "ERREUR_OPERATEUR"
    PROBLEME_MACHINE = "PROBLEME_MACHINE"
    NON_CONFORMITE_PROCESS = "NON_CONFORMITE_PROCESS"
    AUTRE = "AUTRE"


class TypeMaintenance(str, Enum):
    """Type de maintenance."""

    PREVENTIVE = "PREVENTIVE"
    CORRECTIVE = "CORRECTIVE"
    URGENCE = "URGENCE"


class SeveriteAlerte(str, Enum):
    """Sévérité d'une alerte."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class TypeEvenementQualite(str, Enum):
    """Type d'événement qualité: bonne pièce ou rebut."""

    BONNE = "BONNE"
    REBUT = "REBUT"


class StatutProposition(str, Enum):
    """Cycle de vie d'une proposition d'action du superviseur autonome."""

    PROPOSEE = "PROPOSEE"
    APPROUVEE = "APPROUVEE"
    REJETEE = "REJETEE"
    EXECUTEE = "EXECUTEE"
    ECHOUEE = "ECHOUEE"
