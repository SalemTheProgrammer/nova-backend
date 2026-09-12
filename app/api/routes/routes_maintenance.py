"""Historique de maintenance (préventive/corrective/urgence)."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import MaintenanceEvent
from app.models.enums import TypeMaintenance
from app.services import machine_command_service
from app.services.machine_command_service import MachineCommandError
from app.services.risk_service import analyser_risques

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


class RisqueMachineRead(BaseModel):
    machine_id: int
    code: str
    nom: str
    score: float
    nb_pannes_7j: int
    duree_arret_7j_s: float
    jours_depuis_maintenance: int | None
    niveau: str
    recommandation: str


class DemarrerMaintenanceRequest(BaseModel):
    machine_id: int
    type_maintenance: str = "PREVENTIVE"
    description: str | None = None


class ResoudreMaintenanceRequest(BaseModel):
    machine_id: int
    commentaire: str | None = None


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


@router.get("/risques", response_model=list[RisqueMachineRead])
def lister_risques(db: Session = Depends(get_db)) -> list[RisqueMachineRead]:
    return [
        RisqueMachineRead(
            machine_id=r.machine_id,
            code=r.code,
            nom=r.nom,
            score=r.score,
            nb_pannes_7j=r.nb_pannes_7j,
            duree_arret_7j_s=r.duree_arret_7j_s,
            jours_depuis_maintenance=r.jours_depuis_maintenance,
            niveau=r.niveau,
            recommandation=r.recommandation,
        )
        for r in analyser_risques(db)
    ]


@router.post("/demarrer")
def api_demarrer_maintenance(req: DemarrerMaintenanceRequest) -> dict:
    try:
        msg = machine_command_service.demarrer_maintenance(
            req.machine_id,
            type_maintenance=req.type_maintenance,
            description=req.description,
        )
        return {"status": "ok", "message": msg}
    except MachineCommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/resoudre")
def api_resoudre_maintenance(req: ResoudreMaintenanceRequest) -> dict:
    try:
        msg = machine_command_service.resoudre_arret(
            req.machine_id,
            commentaire=req.commentaire,
        )
        return {"status": "ok", "message": msg}
    except MachineCommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
