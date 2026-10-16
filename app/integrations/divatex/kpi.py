"""Formules d'indicateurs de production (pures, sans SQL)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.core.config import get_settings

SEUIL_OBJECTIF_PERIME_JOURS = 7


@dataclass(frozen=True)
class ParametresRendement:
    facteur_temps: float
    appliquer_majoration: bool
    duree_poste_min: int


def parametres_rendement() -> ParametresRendement:
    s = get_settings()
    return ParametresRendement(
        facteur_temps=s.divatex_temps_facteur,
        appliquer_majoration=s.divatex_appliquer_majoration,
        duree_poste_min=s.divatex_duree_poste_min,
    )


def minutes_produites(
    scans: Iterable[tuple[float, int, float]], p: ParametresRendement
) -> float:
    """Σ temps × quantité × facteur, multiplié par la majoration si activée."""
    total = 0.0
    for temps, quantite, majoration in scans:
        minutes = float(temps or 0) * float(quantite or 0) * p.facteur_temps
        if p.appliquer_majoration:
            minutes *= float(majoration or 0)
        total += minutes
    return total


def rendement(minutes_produites: float, minutes_presence: float) -> float | None:
    if minutes_presence <= 0:
        return None
    return minutes_produites / minutes_presence


def taux_atteinte(sortie: int, objectif: int | None) -> float | None:
    if not objectif:
        return None
    return sortie / objectif


def objectif_perime(date_objectif: date, jour: date) -> bool:
    return (jour - date_objectif).days > SEUIL_OBJECTIF_PERIME_JOURS
