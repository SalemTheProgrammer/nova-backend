"""Envoi automatique et périodique du bilan de production (fin de poste).

Désactivé par défaut. Activé par `AUTO_BILAN_ENABLED=true` (+ canal/destinataire)
dans le .env : à chaque heure listée dans `AUTO_BILAN_HEURES`, génère le bilan
d'équipe en PDF et l'envoie sans confirmation opérateur — contrairement à
`envoyer_rapport` (déclenché en conversation, confirmation obligatoire à chaque
envoi), ici l'opt-in fait en configuration tient lieu d'accord préalable.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.services import notify_service, pdf_service

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
    horodatage = f"{datetime.now():%d/%m/%Y %H:%M}"
    with session_scope() as db:
        pdf = pdf_service.generer_bilan_equipe_pdf(db)
    nom_fichier = "bilan-production.pdf"
    sujet = f"Bilan de production Nova — {horodatage}"
    corps = f"{sujet}\nBilan automatique de fin de poste ({heure}), généré par Nova."
    try:
        if settings.auto_bilan_canal == "whatsapp":
            resultat = notify_service.envoyer_whatsapp(
                settings.auto_bilan_destinataire, corps, document=(nom_fichier, pdf)
            )
        else:
            resultat = notify_service.envoyer_email(
                settings.auto_bilan_destinataire, sujet, corps, piece_jointe=(nom_fichier, pdf)
            )
        logger.info("bilan_auto_envoye", heure=heure, canal=settings.auto_bilan_canal, resultat=resultat)
    except AppError as exc:
        logger.warning("bilan_auto_echec", heure=heure, error=exc.message)


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
