"""Simulateur Machine : un endpoint par action, chacun ingère un événement réel.

Chaque action valide la transition (`simulator_service`), écrit l'événement + met à jour
l'état (`event_service`/`machine_state_service`), puis diffuse le nouvel état machine sur
`/ws/dashboard` — c'est ce canal qui fait vivre le tableau de bord en temps réel.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.routes_machines import machine_read
from app.core.access import require_category
from app.core.exceptions import AppError
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import Machine
from app.schemas.event_schema import (
    AlarmRequest,
    CycleTimeRequest,
    DowntimeResolveRequest,
    DowntimeStartRequest,
    GoodUnitRequest,
    MaintenanceEndRequest,
    MaintenanceStartRequest,
    ScrapUnitRequest,
    SensorTagRequest,
    StartMachineRequest,
)
from app.schemas.machine_schema import MachineRead
from app.services import simulator_service
from app.services.websocket_manager import manager

router = APIRouter(
    prefix="/simulateur",
    tags=["simulateur"],
    dependencies=[
        Depends(require_api_key),
        Depends(require_category("Jumeau numérique", "Actions machine")),
    ],
)


def _get_or_404(db: Session, machine_id: int) -> Machine:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine introuvable")
    return machine


async def _appliquer_et_diffuser(db: Session, machine: Machine, action) -> MachineRead:
    try:
        action()
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    db.flush()
    db.refresh(machine)
    lecture = machine_read(db, machine)
    await manager.broadcast({"type": "machine_update", "machine": lecture.model_dump(mode="json")})
    return lecture


@router.post("/machines/{machine_id}/start", response_model=MachineRead)
async def demarrer(
    machine_id: int, payload: StartMachineRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db,
        machine,
        lambda: simulator_service.demarrer(
            db, machine, ordre_fabrication_id=payload.ordre_fabrication_id
        ),
    )


@router.post("/machines/{machine_id}/stop", response_model=MachineRead)
async def arreter(machine_id: int, db: Session = Depends(get_db)) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(db, machine, lambda: simulator_service.arreter(db, machine))


@router.post("/machines/{machine_id}/pause", response_model=MachineRead)
async def pauser(machine_id: int, db: Session = Depends(get_db)) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db, machine, lambda: simulator_service.mettre_en_pause(db, machine)
    )


@router.post("/machines/{machine_id}/alarme", response_model=MachineRead)
async def alarmer(
    machine_id: int, payload: AlarmRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db, machine, lambda: simulator_service.declencher_alarme(db, machine, message=payload.message)
    )


@router.post("/machines/{machine_id}/cycle-time", response_model=MachineRead)
async def changer_cycle(
    machine_id: int, payload: CycleTimeRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db,
        machine,
        lambda: simulator_service.changer_temps_cycle(
            db, machine, temps_cycle_s=payload.temps_cycle_s
        ),
    )


@router.post("/machines/{machine_id}/production/bonne", response_model=MachineRead)
async def produire_bonne(
    machine_id: int, payload: GoodUnitRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db, machine, lambda: simulator_service.produire_bonne(db, machine, quantite=payload.quantite)
    )


@router.post("/machines/{machine_id}/production/rebut", response_model=MachineRead)
async def produire_rebut(
    machine_id: int, payload: ScrapUnitRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db,
        machine,
        lambda: simulator_service.produire_rebut(
            db, machine, quantite=payload.quantite, cause=payload.cause.value
        ),
    )


@router.post("/machines/{machine_id}/arret/declencher", response_model=MachineRead)
async def declencher_arret(
    machine_id: int, payload: DowntimeStartRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db,
        machine,
        lambda: simulator_service.declencher_arret(
            db, machine, cause=payload.cause.value, comment=payload.comment
        ),
    )


@router.post("/machines/{machine_id}/arret/resoudre", response_model=MachineRead)
async def resoudre_arret(
    machine_id: int, payload: DowntimeResolveRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db, machine, lambda: simulator_service.resoudre_arret(db, machine, comment=payload.comment)
    )


@router.post("/machines/{machine_id}/maintenance/demarrer", response_model=MachineRead)
async def demarrer_maintenance(
    machine_id: int, payload: MaintenanceStartRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db,
        machine,
        lambda: simulator_service.demarrer_maintenance(
            db, machine, type_maintenance=payload.type.value, description=payload.description
        ),
    )


@router.post("/machines/{machine_id}/maintenance/terminer", response_model=MachineRead)
async def terminer_maintenance(
    machine_id: int, payload: MaintenanceEndRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db,
        machine,
        lambda: simulator_service.terminer_maintenance(
            db, machine, prochaine_maintenance=payload.prochaine_maintenance
        ),
    )


@router.post("/machines/{machine_id}/tag", response_model=MachineRead)
async def envoyer_tag(
    machine_id: int, payload: SensorTagRequest, db: Session = Depends(get_db)
) -> MachineRead:
    machine = _get_or_404(db, machine_id)
    return await _appliquer_et_diffuser(
        db,
        machine,
        lambda: simulator_service.envoyer_tag(db, machine, tag=payload.tag, valeur=payload.valeur),
    )


# --------------------------------------------------------------------------- #
# Mode auto (la ligne vit toute seule) + scénarios de démonstration
# --------------------------------------------------------------------------- #


@router.get("/auto")
async def statut_auto() -> dict:
    from app.services.auto_simulator import auto_simulator

    return {"actif": auto_simulator.actif}


@router.post("/auto/start")
async def demarrer_auto() -> dict:
    from app.services.auto_simulator import auto_simulator

    auto_simulator.demarrer()
    return {"actif": True}


@router.post("/auto/stop")
async def arreter_auto() -> dict:
    from app.services.auto_simulator import auto_simulator

    auto_simulator.arreter()
    return {"actif": False}


@router.post("/scenarios/{nom}")
async def declencher_scenario(nom: str, db: Session = Depends(get_db)) -> dict:
    """Injecte un incident réaliste ; le superviseur Nova réagit comme en production."""
    from app.services import auto_simulator as sim

    scenarios = {
        "panne-critique": sim.scenario_panne_critique,
        "derive-qualite": sim.scenario_derive_qualite,
        "rupture-stock": sim.scenario_rupture_stock,
    }
    scenario = scenarios.get(nom)
    if scenario is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Scénario inconnu : {nom}. Choix : {', '.join(scenarios)}",
        )
    try:
        message = scenario(db)
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    return {"scenario": nom, "message": message}


@router.post("/reset")
async def reset_atelier(db: Session = Depends(get_db)) -> dict:
    """Remise à zéro complète de l'atelier, des événements et des compteurs."""
    try:
        return await simulator_service.reinitialiser_atelier(db)
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)

