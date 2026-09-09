"""Routes API pour l'ingestion Sparkplug B MQTT et le studio de mapping des tags."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.security import require_api_key
from app.db.session import get_db
from app.models.sparkplug import SparkplugDevice, SparkplugTagMapping
from app.protocols.sparkplug_b.simulator import sparkplug_simulator
from app.schemas.sparkplug_schema import (
    SparkplugCardSwipeRequest,
    SparkplugDeviceRead,
    SparkplugTagMappingCreateOrUpdate,
    SparkplugTagMappingRead,
    SparkplugTickRequest,
)

router = APIRouter(
    prefix="/sparkplug",
    tags=["sparkplug"],
    dependencies=[
        Depends(require_api_key),
    ],
)


@router.get("/devices", response_model=list[SparkplugDeviceRead])
def lister_devices(db: Session = Depends(get_db)) -> list[SparkplugDeviceRead]:
    """Retourne la liste des devices Sparkplug B enregistrés et leurs métriques."""
    devices = db.execute(select(SparkplugDevice).order_by(SparkplugDevice.id)).scalars().all()
    # Si aucun device n'existe encore, on simule l'apparition du premier device
    if not devices:
        sparkplug_simulator.publish_dbirth()
        devices = db.execute(select(SparkplugDevice).order_by(SparkplugDevice.id)).scalars().all()
    return devices


@router.get("/devices/{device_id}", response_model=SparkplugDeviceRead)
def obtenir_device(device_id: int, db: Session = Depends(get_db)) -> SparkplugDeviceRead:
    """Retourne le détail d'un device avec ses règles de mapping."""
    device = db.get(SparkplugDevice, device_id)
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Device #{device_id} introuvable")
    return device


@router.post("/devices/{device_id}/mappings", response_model=SparkplugTagMappingRead)
def configurer_mapping(
    device_id: int,
    payload: SparkplugTagMappingCreateOrUpdate,
    db: Session = Depends(get_db),
) -> SparkplugTagMappingRead:
    """Crée ou met à jour le mapping d'un tag MQTT Sparkplug B vers un KPI MES."""
    device = db.get(SparkplugDevice, device_id)
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Device #{device_id} introuvable")

    # Chercher si un mapping existe déjà pour ce tag
    mapping = db.execute(
        select(SparkplugTagMapping).where(
            SparkplugTagMapping.device_id == device.id,
            SparkplugTagMapping.tag_name == payload.tag_name,
        )
    ).scalar_one_or_none()

    if not mapping:
        mapping = SparkplugTagMapping(
            device_id=device.id,
            tag_name=payload.tag_name,
            target_kpi=payload.target_kpi,
            transformation=payload.transformation,
            formula_param=payload.formula_param,
            description=payload.description,
            actif=payload.actif,
        )
        db.add(mapping)
    else:
        mapping.target_kpi = payload.target_kpi
        mapping.transformation = payload.transformation
        mapping.formula_param = payload.formula_param
        mapping.description = payload.description
        mapping.actif = payload.actif

    db.flush()
    db.refresh(mapping)
    return mapping


@router.delete("/devices/{device_id}/mappings/{mapping_id}")
def supprimer_mapping(device_id: int, mapping_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    """Supprime une règle de mapping."""
    mapping = db.get(SparkplugTagMapping, mapping_id)
    if not mapping or mapping.device_id != device_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Règle introuvable")
    db.delete(mapping)
    db.flush()
    return {"statut": "supprime"}


# ------------------ SIMULATEUR INDUSTRIEL VIRTUEL ------------------ #

@router.post("/simulator/birth")
def simuler_dbirth() -> dict[str, Any]:
    """Envoie une trame DBIRTH officielle avec le catalogue complet des tags."""
    return sparkplug_simulator.publish_dbirth()


@router.post("/simulator/tick")
def simuler_tick(payload: SparkplugTickRequest) -> dict[str, Any]:
    """Envoie une trame DDATA de télémétrie unitaire."""
    return sparkplug_simulator.publish_ddata(
        good_increment=payload.good_increment,
        reject_increment=payload.reject_increment,
        cadence_cpm=payload.cadence_cpm,
        temperature=payload.temperature,
        operator_card=payload.operator_card or "",
    )


@router.post("/simulator/unplanned-stop")
def simuler_arret_non_planifie() -> dict[str, Any]:
    """Simule un incident réel : machine active mais 0 cadence -> déclenche un arrêt non planifié."""
    return sparkplug_simulator.trigger_unplanned_stop()


@router.post("/simulator/swipe-card")
def simuler_badgeage_carte(payload: SparkplugCardSwipeRequest) -> dict[str, Any]:
    """Simule le passage d'une carte opérateur RFID (qualification d'arrêt ou reprise)."""
    return sparkplug_simulator.swipe_operator_card(payload.card_code)


@router.post("/simulator/start-stream")
def demarrer_flux_continu() -> dict[str, Any]:
    """Démarre l'émission en direct toutes les 2 secondes."""
    return sparkplug_simulator.start_live_stream()


@router.post("/simulator/stop-stream")
def arreter_flux_continu() -> dict[str, Any]:
    """Arrête le flux continu."""
    return sparkplug_simulator.stop_live_stream()


@router.get("/simulator/status")
def statut_simulateur() -> dict[str, Any]:
    """Retourne l'état d'exécution du flux continu."""
    return {"is_streaming": sparkplug_simulator.is_running()}
