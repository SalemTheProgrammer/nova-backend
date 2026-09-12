"""Machines : liste, détail, timeline d'événements — état temps réel."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import Machine, MachineEvent
from app.schemas.machine_schema import MachineEventRead, MachineRead
from app.services.machine_read_service import machine_read

router = APIRouter(
    prefix="/machines",
    tags=["machines"],
    dependencies=[
        Depends(require_api_key),
        Depends(require_category("Supervision / MES", "Actions machine")),
    ],
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
