"""Machines : liste, détail, timeline d'événements — état SCADA temps réel."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_api_key
from app.db.session import get_db
from app.models import DowntimeEvent, Machine, MachineEvent
from app.schemas.machine_schema import DowntimeActifRead, MachineEventRead, MachineRead
from app.services import trs_service

router = APIRouter(prefix="/machines", tags=["machines"], dependencies=[Depends(require_api_key)])


def _downtime_actif(db: Session, machine_id: int) -> DowntimeActifRead | None:
    d = db.execute(
        select(DowntimeEvent)
        .where(DowntimeEvent.machine_id == machine_id, DowntimeEvent.end_time.is_(None))
        .order_by(DowntimeEvent.start_time.desc())
    ).scalars().first()
    if d is None:
        return None
    return DowntimeActifRead(
        id=d.id, cause=d.cause, operator_comment=d.operator_comment, start_time=d.start_time
    )


def machine_read(db: Session, machine: Machine) -> MachineRead:
    trs = None
    if machine.temps_cycle_cible_s:
        resultat = trs_service.calculer_trs_machine(db, machine)
        trs = resultat

    return MachineRead(
        id=machine.id,
        code=machine.code,
        nom=machine.nom,
        ligne_production_id=machine.ligne_production_id,
        temps_cycle_cible_s=machine.temps_cycle_cible_s,
        statut=machine.statut,
        ordre_fabrication_id=machine.ordre_fabrication_id,
        numero_of_actif=machine.ordre_fabrication.numero if machine.ordre_fabrication else None,
        temps_cycle_actuel_s=machine.temps_cycle_actuel_s,
        quantite_produite=machine.quantite_produite,
        quantite_bonne=machine.quantite_bonne,
        quantite_rejetee=machine.quantite_rejetee,
        dernier_evenement_at=machine.dernier_evenement_at,
        downtime_actif=_downtime_actif(db, machine.id),
        trs=trs.trs if trs else None,
        tq=trs.tq if trs else None,
        tp=trs.tp if trs else None,
        do=trs.do if trs else None,
    )


def _get_or_404(db: Session, machine_id: int) -> Machine:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine introuvable")
    return machine


@router.get("", response_model=list[MachineRead])
def lister(ligne_production_id: int | None = None, db: Session = Depends(get_db)) -> list[MachineRead]:
    stmt = select(Machine).where(Machine.actif.is_(True))
    if ligne_production_id is not None:
        stmt = stmt.where(Machine.ligne_production_id == ligne_production_id)
    machines = db.execute(stmt.order_by(Machine.code)).scalars()
    return [machine_read(db, m) for m in machines]


@router.get("/{machine_id}", response_model=MachineRead)
def detail(machine_id: int, db: Session = Depends(get_db)) -> MachineRead:
    return machine_read(db, _get_or_404(db, machine_id))


@router.get("/{machine_id}/evenements", response_model=list[MachineEventRead])
def timeline(
    machine_id: int, limit: int = Query(default=100, le=500), db: Session = Depends(get_db)
) -> list[MachineEventRead]:
    _get_or_404(db, machine_id)
    events = db.execute(
        select(MachineEvent)
        .where(MachineEvent.machine_id == machine_id)
        .order_by(MachineEvent.created_at.desc())
        .limit(limit)
    ).scalars()
    return [
        MachineEventRead(
            id=e.id,
            machine_id=e.machine_id,
            ordre_fabrication_id=e.ordre_fabrication_id,
            type=e.type.value,
            payload=e.payload,
            created_at=e.created_at,
        )
        for e in events
    ]
