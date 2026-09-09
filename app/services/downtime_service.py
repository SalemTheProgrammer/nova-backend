"""Service de gestion et qualification des arrêts machines (manuels ou par carte/tag)."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import DowntimeEvent, Machine
from app.models.enums import CauseArret, StatutMachine, TypeEvenementMachine
from app.services import broadcast_service, event_service

logger = get_logger(__name__)


def detecter_arret_non_planifie(
    db: Session,
    machine: Machine,
    seuil_inactivite_secondes: int = 45,
) -> DowntimeEvent | None:
    """Détecte automatiquement si la machine est sous tension mais ne produit pas.

    Règle : si machine.statut == MARCHE et aucun événement de production
    depuis > seuil_inactivite_secondes, déclenche un arrêt non planifié
    en attente de qualification par carte ou opérateur.
    """
    if machine.statut != StatutMachine.MARCHE:
        return None

    maintenant = datetime.utcnow()
    dernier = machine.dernier_evenement_at or machine.created_at
    ecart_s = (maintenant - dernier).total_seconds()

    if ecart_s < seuil_inactivite_secondes:
        return None

    # Vérifie si un arrêt est déjà ouvert
    arret_ouvert = db.execute(
        select(DowntimeEvent).where(
            and_(
                DowntimeEvent.machine_id == machine.id,
                DowntimeEvent.end_time.is_(None),
            )
        )
    ).scalar_one_or_none()

    if arret_ouvert:
        return arret_ouvert

    # Déclenche l'événement d'arrêt non planifié automatique
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.DOWNTIME_STARTED,
        payload={
            "cause": CauseArret.MICRO_ARRET.value,
            "commentaire": "Détection automatique : machine sous tension sans production",
            "non_planifie": True,
        },
    )

    machine.statut = StatutMachine.ARRET
    db.flush()
    broadcast_service.diffuser_machine(db, machine)

    return db.execute(
        select(DowntimeEvent).where(
            and_(
                DowntimeEvent.machine_id == machine.id,
                DowntimeEvent.end_time.is_(None),
            )
        )
    ).scalar_one_or_none()


def qualifier_ou_creer_arret(
    db: Session,
    *,
    machine: Machine,
    cause: CauseArret,
    commentaire: str | None = None,
) -> DowntimeEvent:
    """Qualifie un arrêt en cours ou crée un arrêt immédiat avec la cause donnée (ex: badgeage carte)."""
    arret_ouvert = db.execute(
        select(DowntimeEvent).where(
            and_(
                DowntimeEvent.machine_id == machine.id,
                DowntimeEvent.end_time.is_(None),
            )
        )
    ).scalar_one_or_none()

    if arret_ouvert:
        # Qualification de l'arrêt existant
        arret_ouvert.cause = cause
        if commentaire:
            arret_ouvert.operator_comment = commentaire
        db.flush()
        broadcast_service.diffuser_machine(db, machine)
        logger.info("arret_qualifie", machine_code=machine.code, cause=cause.value)
        return arret_ouvert

    # Pas d'arrêt ouvert : on en crée un nouveau qualifié
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.DOWNTIME_STARTED,
        payload={"cause": cause.value, "commentaire": commentaire or "Déclaré par carte tag"},
    )
    machine.statut = StatutMachine.ARRET
    db.flush()
    broadcast_service.diffuser_machine(db, machine)

    nouveau = db.execute(
        select(DowntimeEvent).where(
            and_(
                DowntimeEvent.machine_id == machine.id,
                DowntimeEvent.end_time.is_(None),
            )
        )
    ).scalar_one_or_none()

    return nouveau or DowntimeEvent(machine_id=machine.id, cause=cause, start_time=datetime.utcnow())
