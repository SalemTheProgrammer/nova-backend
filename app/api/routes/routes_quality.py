"""Suivi qualité : événements bonne pièce/rebut, résumé et causes."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_api_key
from app.db.session import get_db
from app.models import QualityEvent
from app.models.enums import TypeEvenementQualite
from app.schemas.quality_schema import QualiteResume, QualityEventRead

router = APIRouter(prefix="/qualite", tags=["qualite"], dependencies=[Depends(require_api_key)])


@router.get("/evenements", response_model=list[QualityEventRead])
def lister(
    machine_id: int | None = None,
    limit: int = Query(default=200, le=1000),
    db: Session = Depends(get_db),
) -> list[QualityEventRead]:
    stmt = select(QualityEvent)
    if machine_id is not None:
        stmt = stmt.where(QualityEvent.machine_id == machine_id)
    stmt = stmt.order_by(QualityEvent.created_at.desc()).limit(limit)
    return [
        QualityEventRead(
            id=e.id,
            machine_id=e.machine_id,
            code_machine=e.machine.code,
            ordre_fabrication_id=e.ordre_fabrication_id,
            type=e.type,
            quantite=e.quantite,
            cause=e.cause,
            created_at=e.created_at,
        )
        for e in db.execute(stmt).scalars()
    ]


@router.get("/resume", response_model=QualiteResume)
def resume(machine_id: int | None = None, db: Session = Depends(get_db)) -> QualiteResume:
    stmt = select(QualityEvent)
    if machine_id is not None:
        stmt = stmt.where(QualityEvent.machine_id == machine_id)
    events = list(db.execute(stmt).scalars())

    bonne = sum(e.quantite for e in events if e.type == TypeEvenementQualite.BONNE)
    rejetee = sum(e.quantite for e in events if e.type == TypeEvenementQualite.REBUT)
    total = bonne + rejetee
    taux = Decimal(rejetee) / Decimal(total) if total else Decimal("0")

    causes: dict[str, int] = {}
    for e in events:
        if e.cause is not None:
            causes[e.cause.value] = causes.get(e.cause.value, 0) + e.quantite

    return QualiteResume(
        quantite_bonne=bonne, quantite_rejetee=rejetee, taux_rebut=taux, causes=causes
    )
