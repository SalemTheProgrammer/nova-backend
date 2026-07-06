"""Propositions du superviseur autonome : journal + décision opérateur."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_api_key
from app.db.session import get_db
from app.models import AgentProposal
from app.models.enums import StatutProposition
from app.schemas.agent_schema import AgentProposalRead
from app.services import supervisor_service

router = APIRouter(prefix="/agent", tags=["agent-superviseur"], dependencies=[Depends(require_api_key)])


def _lire(p: AgentProposal) -> AgentProposalRead:
    return AgentProposalRead(
        id=p.id,
        type=p.type,
        severite=p.severite.value,
        titre=p.titre,
        diagnostic=p.diagnostic,
        action_libelle=p.action_libelle,
        action=p.action,
        statut=p.statut.value,
        machine_id=p.machine_id,
        ordre_fabrication_id=p.ordre_fabrication_id,
        resultat=p.resultat,
        created_at=p.created_at,
        decided_at=p.decided_at,
    )


@router.get("/propositions", response_model=list[AgentProposalRead])
def lister_propositions(
    en_attente_seulement: bool = Query(default=False),
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
) -> list[AgentProposalRead]:
    stmt = select(AgentProposal).order_by(AgentProposal.created_at.desc()).limit(limit)
    if en_attente_seulement:
        stmt = stmt.where(AgentProposal.statut == StatutProposition.PROPOSEE)
    return [_lire(p) for p in db.execute(stmt).scalars()]


@router.post("/propositions/{proposition_id}/approuver", response_model=AgentProposalRead)
def approuver(proposition_id: int, db: Session = Depends(get_db)) -> AgentProposalRead:
    return _lire(supervisor_service.decider(db, proposition_id, approuver=True))


@router.post("/propositions/{proposition_id}/rejeter", response_model=AgentProposalRead)
def rejeter(proposition_id: int, db: Session = Depends(get_db)) -> AgentProposalRead:
    return _lire(supervisor_service.decider(db, proposition_id, approuver=False))
