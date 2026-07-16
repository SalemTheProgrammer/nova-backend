"""Horloge de l'usine : conversions UTC ↔ heure locale (Africa/Tunis).

La base stocke tout en UTC naïf (`datetime.utcnow()`), mais TOUT ce qui est
montré à l'opérateur (chat, diagnostics du superviseur, rapports) doit être en
heure locale de l'usine. La Tunisie est à UTC+1 toute l'année depuis 2009 : un
décalage fixe évite la dépendance optionnelle `tzdata`, absente de certaines
installations Python Windows utilisées en démo (même choix que `agent_node`).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

TZ_USINE = timezone(timedelta(hours=1), name="Africa/Tunis")


def heure_usine() -> datetime:
    """Maintenant, en heure locale de l'usine (aware)."""
    return datetime.now(TZ_USINE)


def en_heure_usine(dt_utc: datetime) -> datetime:
    """Convertit un datetime UTC (naïf, tel que stocké en base) en heure usine."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(TZ_USINE)


def date_usine(dt_utc: datetime) -> date:
    """La date côté usine d'un datetime UTC — à utiliser pour comparer une
    projection à une échéance client (les échéances sont des dates locales) :
    autour de minuit, la date UTC peut être en retard d'un jour."""
    return en_heure_usine(dt_utc).date()
