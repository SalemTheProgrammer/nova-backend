"""Diffusion temps réel vers /ws/dashboard depuis du code synchrone.

Les outils agent, l'ingestion Sparkplug et les boucles d'arrière-plan
s'exécutent hors de la boucle asyncio du serveur : ce module encapsule
`broadcast_threadsafe` pour pousser les mises à jour machine et les messages
agent sans bloquer ni casser la transaction appelante.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import Machine
from app.services.machine_read_service import machine_read
from app.services.websocket_manager import manager

logger = get_logger(__name__)


def diffuser(message: dict) -> None:
    manager.broadcast_threadsafe(message)


def _message_machine(db: Session, machine: Machine) -> dict:
    return {"type": "machine_update", "machine": machine_read(db, machine).model_dump(mode="json")}


def diffuser_machine(db: Session, machine: Machine) -> None:
    """Diffuse l'état complet d'une machine, lu dans la session de l'appelant.

    Appeler APRÈS le commit : le frontend relit l'API REST à réception.
    """
    try:
        diffuser(_message_machine(db, machine))
    except Exception:  # noqa: BLE001 — la diffusion est au mieux
        logger.warning("diffuser_machine_failed", machine_id=machine.id)


def diffuser_machines_par_id(machine_ids: Iterable[int]) -> None:
    """Relit dans une session propre (donc l'état committé) et diffuse ces machines."""
    ids = list(dict.fromkeys(machine_ids))
    if not ids:
        return
    try:
        with session_scope() as db:
            for machine in db.execute(select(Machine).where(Machine.id.in_(ids))).scalars():
                diffuser(_message_machine(db, machine))
    except Exception:  # noqa: BLE001 — la diffusion est au mieux
        logger.warning("diffuser_machines_failed", machine_ids=ids)
