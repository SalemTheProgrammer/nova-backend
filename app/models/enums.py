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
