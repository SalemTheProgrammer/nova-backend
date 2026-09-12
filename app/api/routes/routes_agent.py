"""Propositions du superviseur autonome : journal + décision opérateur."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import get_current_user, require_api_key
from app.db.session import get_db
from app.models import AgentProposal
from app.models.enums import StatutProposition
from app.schemas.agent_schema import AgentProposalRead, AutonomieRead, AutonomieUpdate
from app.services import supervisor_service

# Décisions du superviseur, tranchées depuis les cartes du chat Nova (voir
# DecisionMessage.tsx) par tout utilisateur connecté — pas réservé à l'admin,
# juste authentifié par utilisateur plutôt que par la seule clé API partagée.
router = APIRouter(
    prefix="/agent",
    tags=["agent-superviseur"],
    dependencies=[Depends(require_api_key), Depends(get_current_user)],
)


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
        decideur=p.decideur,
        execution_auto_at=p.execution_auto_at,
        risque=supervisor_service.RISQUE_PAR_TYPE.get(p.type, supervisor_service.RISQUE_MOYEN),
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


@router.get("/autonomie", response_model=AutonomieRead)
def lire_autonomie() -> AutonomieRead:
    return AutonomieRead(
        mode=supervisor_service.mode_autonomie(),
        delai_moyen_s=get_settings().autopilote_delai_moyen_s,
    )


@router.post("/autonomie", response_model=AutonomieRead)
def definir_autonomie(payload: AutonomieUpdate) -> AutonomieRead:
    try:
        mode = supervisor_service.definir_mode_autonomie(payload.mode)
    except AppError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message) from exc
    return AutonomieRead(mode=mode, delai_moyen_s=get_settings().autopilote_delai_moyen_s)
