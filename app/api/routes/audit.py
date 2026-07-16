"""Consultation du journal d'audit des actions (lecture seule, ajout seul).

Chaque action irréversible/sortante exécutée (outil confirmé en conversation ou
proposition du superviseur approuvée) y est attribuée : quoi, quand, depuis quel
canal, par qui. Voir `models/audit.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.security import require_api_key
from app.db.session import session_scope
from app.models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_api_key)])


class EntreeAudit(BaseModel):
    id: int
    horodatage: str
    action: str
    arguments: dict | None
    source: str
    canal: str
    identite: str | None
    thread_id: str | None
    resultat: str | None


@router.get("", response_model=list[EntreeAudit])
def lister_audit(limit: int = Query(default=50, ge=1, le=500)) -> list[EntreeAudit]:
    with session_scope() as db:
        entrees = db.execute(
            select(AuditLog).order_by(AuditLog.horodatage.desc(), AuditLog.id.desc()).limit(limit)
        ).scalars().all()
        return [
            EntreeAudit(
                id=e.id,
                horodatage=e.horodatage.isoformat(),
                action=e.action,
                arguments=e.arguments,
                source=e.source,
                canal=e.canal,
                identite=e.identite,
                thread_id=e.thread_id,
                resultat=e.resultat,
            )
            for e in entrees
        ]
