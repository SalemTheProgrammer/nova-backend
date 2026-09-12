"""Qualification des arrêts machine (cause saisie par l'opérateur, ex. badge RFID)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import DowntimeEvent, Machine
from app.models.enums import CauseArret

logger = get_logger(__name__)


def qualifier_arret_ouvert(
    db: Session,
    *,
    machine: Machine,
    cause: CauseArret,
    commentaire: str | None = None,
) -> DowntimeEvent | None:
    """Qualifie l'arrêt en cours de la machine (cause + commentaire).

    Ne crée jamais d'arrêt : un arrêt commence quand l'automate le signale
    (état PANNE), pas quand un badge est présenté. Renvoie None s'il n'y a
    aucun arrêt ouvert.
    """
    arret = db.execute(
        select(DowntimeEvent)
        .where(DowntimeEvent.machine_id == machine.id, DowntimeEvent.end_time.is_(None))
        .order_by(DowntimeEvent.start_time.desc())
    ).scalars().first()
    if arret is None:
        return None
    arret.cause = cause
    if commentaire:
        arret.operator_comment = commentaire
    db.flush()
    logger.info("arret_qualifie", machine=machine.code, cause=cause.value)
    return arret
