"""Envoi automatique et périodique du rapport du matin (par défaut R1).

Désactivé par défaut. Activé par `AUTO_BILAN_ENABLED=true` (+ canal/destinataire)
dans le .env : à chaque heure listée dans `AUTO_BILAN_HEURES`, le rapport
`AUTO_BILAN_RAPPORT` part en PDF sans confirmation opérateur — l'opt-in fait en
configuration tient lieu d'accord préalable.

Sans destinataire configuré, ou base DIVA injoignable, l'envoi du créneau est
journalisé et ignoré (la boucle continue).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.integrations.divatex import connexion
from app.services import scheduler_service, tunicotex_pdf

logger = get_logger(__name__)

INTERVALLE_VERIF_S = 60.0

# Dernière date d'envoi par heure configurée ("HH:MM" -> date) — évite un double
# envoi si la boucle tourne plus d'une fois pendant la même minute.
_dernier_envoi: dict[str, date] = {}


def _heures_configurees() -> list[str]:
    settings = get_settings()
    return [h.strip() for h in settings.auto_bilan_heures.split(",") if h.strip()]


def _envoyer_bilan_auto(heure: str) -> None:
    settings = get_settings()
    if not settings.auto_bilan_destinataire:
        logger.warning("bilan_auto_sans_destinataire", heure=heure)
        return
    try:
        resultat = scheduler_service.envoyer_rapport_pdf(
            settings.auto_bilan_canal,
            settings.auto_bilan_destinataire,
            settings.auto_bilan_rapport,
            tunicotex_pdf.ParametresRapport(),
        )
    except connexion.DivatexIndisponible:
        logger.warning("rapport_non_disponible", heure=heure, canal=settings.auto_bilan_canal)
    except AppError as exc:
        logger.error("bilan_auto_echec", heure=heure, error=exc.message)
    else:
        logger.info("bilan_auto_envoye", heure=heure, resultat=resultat)


def _tick() -> None:
    settings = get_settings()
    if not settings.auto_bilan_enabled or not settings.auto_bilan_destinataire:
        return
    maintenant = datetime.now()
    heure_courante = f"{maintenant:%H:%M}"
    aujourdhui = maintenant.date()
    for heure in _heures_configurees():
        if heure != heure_courante or _dernier_envoi.get(heure) == aujourdhui:
            continue
        _dernier_envoi[heure] = aujourdhui
        _envoyer_bilan_auto(heure)


async def boucle_bilan_auto(intervalle_s: float = INTERVALLE_VERIF_S) -> None:
    """Boucle infinie : vérifie chaque minute si une heure de bilan configurée est atteinte."""
    logger.info("bilan_auto_demarre", intervalle_s=intervalle_s)
    while True:
        try:
            await asyncio.to_thread(_tick)
        except asyncio.CancelledError:
            logger.info("bilan_auto_arrete")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("bilan_auto_tick_failed")
        await asyncio.sleep(intervalle_s)
