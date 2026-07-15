"""Exécution des envois sortants programmés (« dans 5 minutes, envoie… »).

L'agent enregistre un `EnvoiPlanifie` (outil `planifier_envoi`, confirmation
opérateur obligatoire) ; la boucle ici vérifie toutes les quelques secondes et
exécute les envois arrivés à échéance. Le contenu est produit AU MOMENT de
l'envoi (bilan avec données fraîches, PDF du document relu sur disque).

Persisté en base : les envois survivent à un redémarrage. Un envoi retrouvé
trop en retard (backend éteint) est marqué MANQUE plutôt qu'envoyé périmé sans
prévenir. Une erreur transitoire (service WhatsApp pas encore lancé…) est
retentée avant d'abandonner.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import DocumentRag, EnvoiPlanifie, OrdreFabrication
from app.models.envoi_planifie import (
    STATUT_ANNULE,
    STATUT_ECHEC,
    STATUT_EN_ATTENTE,
    STATUT_ENVOYE,
    STATUT_MANQUE,
    TYPE_BILAN,
    TYPE_DOCUMENT,
    TYPE_MESSAGE,
)
from app.services import document_files, notify_service, pdf_service

logger = get_logger(__name__)

INTERVALLE_VERIF_S = 10.0
# Backend éteint au moment prévu : au-delà de ce retard, l'envoi est MANQUE.
RETARD_MAX = timedelta(minutes=30)
# Erreur à l'envoi : nouvel essai après ce délai, TENTATIVES_MAX fois en tout.
DELAI_REESSAI = timedelta(seconds=60)
TENTATIVES_MAX = 3


def programmer(
    db: Session,
    *,
    type_envoi: str,
    canal: str,
    destinataire: str,
    execute_at: datetime,
    of_numero: str | None = None,
    document_id: int | None = None,
    contenu: str | None = None,
    demande_par: str | None = None,
) -> EnvoiPlanifie:
    """Enregistre un envoi à exécuter à `execute_at` (la boucle s'en charge)."""
    if type_envoi not in (TYPE_BILAN, TYPE_DOCUMENT, TYPE_MESSAGE):
        raise ValueError(f"type_envoi invalide : {type_envoi!r}")
    envoi = EnvoiPlanifie(
        type_envoi=type_envoi,
        canal=canal,
        destinataire=destinataire,
        execute_at=execute_at,
        of_numero=of_numero,
        document_id=document_id,
        contenu=contenu,
        demande_par=demande_par,
    )
    db.add(envoi)
    db.flush()
    logger.info(
        "envoi_planifie_cree",
        envoi_id=envoi.id,
        type_envoi=type_envoi,
        canal=canal,
        execute_at=str(execute_at),
    )
    return envoi


def annuler(db: Session, envoi_id: int) -> EnvoiPlanifie | None:
    """Annule un envoi encore en attente. None si introuvable ou déjà parti."""
    envoi = db.get(EnvoiPlanifie, envoi_id)
    if envoi is None or envoi.statut != STATUT_EN_ATTENTE:
        return None
    envoi.statut = STATUT_ANNULE
    envoi.executed_at = datetime.now()
    db.flush()
    logger.info("envoi_planifie_annule", envoi_id=envoi_id)
    return envoi


def envois_en_attente(db: Session) -> list[EnvoiPlanifie]:
    return list(
        db.execute(
            select(EnvoiPlanifie)
            .where(EnvoiPlanifie.statut == STATUT_EN_ATTENTE)
            .order_by(EnvoiPlanifie.execute_at)
        ).scalars()
    )


def _envoyer(
    canal: str, destinataire: str, sujet: str, corps: str, document: tuple[str, bytes] | None
) -> str:
    if canal == "email":
        return notify_service.envoyer_email(destinataire, sujet, corps, piece_jointe=document)
    return notify_service.envoyer_whatsapp(destinataire, corps, document=document)


def _executer(db: Session, envoi: EnvoiPlanifie) -> str:
    """Produit le contenu (frais) et envoie. Lève AppError en cas d'échec."""
    horodatage = f"{datetime.now():%d/%m/%Y %H:%M}"
    if envoi.type_envoi == TYPE_MESSAGE:
        sujet = f"Message programmé de Nova — {horodatage}"
        return _envoyer(envoi.canal, envoi.destinataire, sujet, envoi.contenu or "", None)

    if envoi.type_envoi == TYPE_DOCUMENT:
        document = db.get(DocumentRag, envoi.document_id) if envoi.document_id else None
        if document is None:
            raise AppError(f"Document n°{envoi.document_id} introuvable (supprimé entre-temps ?).")
        nom_fichier, contenu_pdf = document_files.lire_pdf(document)
        sujet = f"Document « {document.nom} » — {horodatage}"
        corps = f"{sujet}\nEnvoi programmé, transmis par Nova."
        return _envoyer(envoi.canal, envoi.destinataire, sujet, corps, (nom_fichier, contenu_pdf))

    # TYPE_BILAN — généré maintenant : les chiffres sont ceux de l'heure d'envoi.
    if envoi.of_numero:
        of: OrdreFabrication | None = None
        if envoi.of_numero.isdigit():
            of = db.get(OrdreFabrication, int(envoi.of_numero))
        if of is None:
            of = db.execute(
                select(OrdreFabrication).where(OrdreFabrication.numero == envoi.of_numero)
            ).scalars().first()
        if of is None:
            raise AppError(f"OF introuvable : {envoi.of_numero}")
        pdf = pdf_service.generer_bilan_of_pdf(db, of)
        nom_fichier = f"bilan-of-{of.numero.replace('/', '-')}.pdf"
        sujet = f"Bilan OF {of.numero} — {horodatage}"
    else:
        pdf = pdf_service.generer_bilan_equipe_pdf(db)
        nom_fichier = "bilan-production.pdf"
        sujet = f"Bilan de production Nova — {horodatage}"
    corps = f"{sujet}\nEnvoi programmé, bilan en pièce jointe (PDF), généré par Nova."
    return _envoyer(envoi.canal, envoi.destinataire, sujet, corps, (nom_fichier, pdf))


def _traiter_envoi_du(db: Session, envoi: EnvoiPlanifie, maintenant: datetime) -> None:
    if maintenant - envoi.execute_at > RETARD_MAX:
        envoi.statut = STATUT_MANQUE
        envoi.executed_at = maintenant
        envoi.resultat = (
            "Non envoyé : le backend était arrêté à l'heure prévue "
            f"({envoi.execute_at:%d/%m/%Y %H:%M})."
        )
        logger.warning("envoi_planifie_manque", envoi_id=envoi.id)
        return
    try:
        resultat = _executer(db, envoi)
    except AppError as exc:
        envoi.tentatives += 1
        if envoi.tentatives >= TENTATIVES_MAX:
            envoi.statut = STATUT_ECHEC
            envoi.executed_at = maintenant
            envoi.resultat = f"Échec après {envoi.tentatives} tentatives : {exc.message}"
            logger.error("envoi_planifie_echec", envoi_id=envoi.id, error=exc.message)
        else:
            envoi.execute_at = maintenant + DELAI_REESSAI
            envoi.resultat = f"Tentative {envoi.tentatives} échouée ({exc.message}), nouvel essai."
            logger.warning(
                "envoi_planifie_reessai",
                envoi_id=envoi.id,
                tentative=envoi.tentatives,
                error=exc.message,
            )
        return
    envoi.statut = STATUT_ENVOYE
    envoi.executed_at = maintenant
    envoi.resultat = resultat
    logger.info("envoi_planifie_envoye", envoi_id=envoi.id, resultat=resultat)


def _tick() -> None:
    maintenant = datetime.now()
    with session_scope() as db:
        dus = list(
            db.execute(
                select(EnvoiPlanifie)
                .where(
                    EnvoiPlanifie.statut == STATUT_EN_ATTENTE,
                    EnvoiPlanifie.execute_at <= maintenant,
                )
                .order_by(EnvoiPlanifie.execute_at)
            ).scalars()
        )
        for envoi in dus:
            _traiter_envoi_du(db, envoi, maintenant)


async def boucle_envois_planifies(intervalle_s: float = INTERVALLE_VERIF_S) -> None:
    """Boucle infinie : exécute les envois programmés arrivés à échéance."""
    logger.info("envois_planifies_demarre", intervalle_s=intervalle_s)
    while True:
        try:
            await asyncio.to_thread(_tick)
        except asyncio.CancelledError:
            logger.info("envois_planifies_arrete")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("envois_planifies_tick_failed")
        await asyncio.sleep(intervalle_s)
