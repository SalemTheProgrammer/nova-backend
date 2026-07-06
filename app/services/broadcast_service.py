"""Diffusion temps réel vers /ws/dashboard depuis du code synchrone.

Les outils agent et les boucles d'arrière-plan s'exécutent hors de la boucle
asyncio du serveur : ce module encapsule `broadcast_threadsafe` pour pousser
les mises à jour machine et les messages agent sans bloquer ni casser la
transaction appelante.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Machine
from app.services.websocket_manager import manager

logger = get_logger(__name__)


def diffuser(message: dict) -> None:
    manager.broadcast_threadsafe(message)


def diffuser_machine(db: Session, machine: Machine) -> None:
    """Diffuse l'état complet d'une machine (même payload que le simulateur)."""
    # Import paresseux : machine_read vit dans la couche routes.
    from app.api.routes.routes_machines import machine_read

    try:
        lecture = machine_read(db, machine)
    except Exception:  # noqa: BLE001
        logger.warning("diffuser_machine_failed", machine_id=machine.id)
        return
    diffuser({"type": "machine_update", "machine": lecture.model_dump(mode="json")})
