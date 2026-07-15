"""Suivi des arrêts machine : actifs et historique."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import require_api_key
from app.db.session import get_db
from app.models import DowntimeEvent
from app.models.enums import CauseArret
from app.schemas.downtime_schema import DowntimePage, DowntimeRead

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


@router.get("", response_model=DowntimePage)
def lister(
    machine_id: int | None = None,
    cause: CauseArret | None = None,
    actifs_seulement: bool = False,
    resolus_seulement: bool = False,
    date_debut: datetime | None = None,
    date_fin: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=5, ge=1, le=200),
    db: Session = Depends(get_db),
) -> DowntimePage:
    stmt = select(DowntimeEvent)
    if machine_id is not None:
        stmt = stmt.where(DowntimeEvent.machine_id == machine_id)
    if cause is not None:
        stmt = stmt.where(DowntimeEvent.cause == cause)
    if actifs_seulement:
        stmt = stmt.where(DowntimeEvent.end_time.is_(None))
    elif resolus_seulement:
        stmt = stmt.where(DowntimeEvent.end_time.isnot(None))
    if date_debut is not None:
        stmt = stmt.where(DowntimeEvent.start_time >= date_debut)
    if date_fin is not None:
        stmt = stmt.where(DowntimeEvent.start_time <= date_fin)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    stmt = (
        stmt.order_by(DowntimeEvent.start_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [_read(d) for d in db.execute(stmt).scalars()]
    return DowntimePage(items=items, total=total, page=page, page_size=page_size)
