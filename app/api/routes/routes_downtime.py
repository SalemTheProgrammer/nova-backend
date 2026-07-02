"""Suivi des arrêts machine : actifs et historique."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_api_key
from app.db.session import get_db
from app.models import DowntimeEvent
from app.schemas.downtime_schema import DowntimeRead

router = APIRouter(prefix="/arrets", tags=["arrets"], dependencies=[Depends(require_api_key)])


def _read(d: DowntimeEvent) -> DowntimeRead:
    fin = d.end_time or datetime.utcnow()
    duree = Decimal(str((fin - d.start_time).total_seconds()))
    return DowntimeRead(
        id=d.id,
        machine_id=d.machine_id,
        code_machine=d.machine.code,
        ordre_fabrication_id=d.ordre_fabrication_id,
        cause=d.cause,
        operator_comment=d.operator_comment,
        start_time=d.start_time,
        end_time=d.end_time,
        duree_s=duree,
    )


@router.get("", response_model=list[DowntimeRead])
def lister(
    machine_id: int | None = None,
    actifs_seulement: bool = False,
    limit: int = Query(default=200, le=1000),
    db: Session = Depends(get_db),
) -> list[DowntimeRead]:
    stmt = select(DowntimeEvent)
    if machine_id is not None:
        stmt = stmt.where(DowntimeEvent.machine_id == machine_id)
    if actifs_seulement:
        stmt = stmt.where(DowntimeEvent.end_time.is_(None))
    stmt = stmt.order_by(DowntimeEvent.start_time.desc()).limit(limit)
    return [_read(d) for d in db.execute(stmt).scalars()]
