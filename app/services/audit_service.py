"""Écriture du journal d'audit (voir `models/audit.py`).

L'audit ne doit JAMAIS faire échouer l'action qu'il trace : toute erreur
d'écriture est loggée puis avalée.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.audit import AuditLog

logger = get_logger(__name__)


def _canal_depuis_thread(thread_id: str | None) -> tuple[str, str | None]:
    """Déduit (canal, identité) du thread de conversation.

    Les threads WhatsApp sont nommés `wa:+216…` (voir routes/whatsapp.py) : le
    numéro EST l'identité de l'opérateur. Sur le web, pas encore de login —
    l'identité est la console elle-même.
    """
    if thread_id and thread_id.startswith("wa:"):
        return "whatsapp", thread_id[3:]
    return "web", "console-web"


def enregistrer_action(
    *,
    action: str,
    arguments: dict | None = None,
    source: str = "agent",
    thread_id: str | None = None,
    canal: str | None = None,
    identite: str | None = None,
    resultat: str | None = None,
) -> None:
    canal_deduit, identite_deduite = _canal_depuis_thread(thread_id)
    entree = AuditLog(
        action=action,
        arguments=arguments,
        source=source,
        canal=canal or canal_deduit,
        identite=identite or identite_deduite,
        thread_id=thread_id,
        resultat=resultat,
    )
    try:
        with session_scope() as db:
            db.add(entree)
    except Exception:  # noqa: BLE001 — l'audit ne bloque jamais l'action
        logger.exception("audit_write_failed", action=action)
