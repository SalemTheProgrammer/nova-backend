"""Résolution des dates relatives saisies par l'agent (« hier », « 26/08 »…)."""
from __future__ import annotations

import re
from datetime import date, timedelta

_RELATIFS = {"aujourd'hui": 0, "aujourd’hui": 0, "hier": 1, "avant-hier": 2, "avant hier": 2}
_JJ_MM = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{4}))?$")
_PERIODE_PAR_DEFAUT_JOURS = 7


def resoudre_jour(texte: str | None, ref: date) -> date:
    """Convertit un texte en date ; ``None`` ou vide = la date de référence."""
    brut = (texte or "").strip().lower()
    if not brut:
        return ref
    if brut in _RELATIFS:
        return ref - timedelta(days=_RELATIFS[brut])
    m = _JJ_MM.match(brut)
    try:
        if m:
            annee = int(m[3]) if m[3] else ref.year
            return date(annee, int(m[2]), int(m[1]))
        return date.fromisoformat(brut)
    except ValueError:
        raise ValueError(f"Date non comprise : {texte}") from None


def resoudre_periode(jours: int | None, ref: date) -> tuple[date, date]:
    """Période de ``jours`` jours se terminant à ``ref`` (7 par défaut)."""
    n = jours if jours is not None else _PERIODE_PAR_DEFAUT_JOURS
    if n < 1:
        raise ValueError("Période invalide.")
    return ref - timedelta(days=n - 1), ref
