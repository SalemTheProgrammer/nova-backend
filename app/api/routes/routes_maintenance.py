"""Historique de maintenance (préventive/corrective/urgence)."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import MaintenanceEvent
from app.models.enums import TypeMaintenance

router = APIRouter(
    prefix="/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(require_api_key), Depends(require_category("Actions machine"))],
)


class MaintenanceEventRead(BaseModel):
    id: int
    machine_id: int
    code_machine: str
    type: TypeMaintenance
    description: str | None
    start_time: datetime
    end_time: datetime | None
    prochaine_maintenance: date | None


@router.get("", response_model=list[MaintenanceEventRead])
def lister(
    machine_id: int | None = None,
    limit: int = Query(default=200, le=1000),
    db: Session = Depends(get_db),
) -> list[MaintenanceEventRead]:
    stmt = select(MaintenanceEvent)
    if machine_id is not None:
        stmt = stmt.where(MaintenanceEvent.machine_id == machine_id)
    stmt = stmt.order_by(MaintenanceEvent.start_time.desc()).limit(limit)
    return [
        MaintenanceEventRead(
            id=e.id,
            machine_id=e.machine_id,
            code_machine=e.machine.code,
            type=e.type,
            description=e.description,
            start_time=e.start_time,
            end_time=e.end_time,
            prochaine_maintenance=e.prochaine_maintenance,
        )
        for e in db.execute(stmt).scalars()
    ]
