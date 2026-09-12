"""Sparkplug B : état de l'hôte MQTT, registre des automates, règles de mapping.

Lecture : utilisateurs ayant accès à la supervision ou aux actions machine.
Écriture (rattachement machine, règles, re-naissance) : administrateur.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.config import get_settings
from app.core.security import require_admin, require_api_key
from app.db.session import get_db
from app.models import Machine
from app.models.sparkplug import SparkplugDevice, SparkplugTagMapping
from app.protocols.sparkplug_b import runtime
from app.schemas.sparkplug_schema import (
    RebirthRead,
    SparkplugDeviceBind,
    SparkplugDeviceRead,
    SparkplugStatusRead,
    SparkplugTagMappingCreateOrUpdate,
    SparkplugTagMappingRead,
)
from app.services import machine_command_service

router = APIRouter(
    prefix="/sparkplug",
    tags=["sparkplug"],
    dependencies=[
        Depends(require_api_key),
        Depends(require_category("Supervision / MES", "Actions machine")),
    ],
)


def _device_read(device: SparkplugDevice) -> SparkplugDeviceRead:
    return SparkplugDeviceRead(
        id=device.id,
        name=device.name,
        group_id=device.group_id,
        edge_node_id=device.edge_node_id,
        device_id=device.device_id,
        machine_id=device.machine_id,
        machine_code=device.machine.code if device.machine else None,
        broker_url=device.broker_url,
        online=device.online,
        last_birth_at=device.last_birth_at,
        last_data_at=device.last_data_at,
        available_metrics=device.available_metrics,
        last_values=device.last_values,
        mappings=[SparkplugTagMappingRead.model_validate(m) for m in device.mappings],
    )


def _device_ou_404(db: Session, device_id: int) -> SparkplugDevice:
    device = db.get(SparkplugDevice, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Automate #{device_id} introuvable.")
    return device


@router.get("/status", response_model=SparkplugStatusRead)
def statut_hote() -> SparkplugStatusRead:
    """État de la connexion MQTT de l'hôte Sparkplug."""
    en_attente = len(machine_command_service.registry)
    host = runtime.get_host()
    if host is None:
        settings = get_settings()
        return SparkplugStatusRead(
            enabled=False,
            connected=False,
            broker=None,
            group_id=settings.sparkplug_group_id,
            host_id=settings.sparkplug_host_id,
            last_error="MQTT désactivé (MQTT_ENABLED=false).",
            queue_depth=0,
            edge_nodes=0,
            commandes_en_attente=en_attente,
        )
    return SparkplugStatusRead(**host.status(), commandes_en_attente=en_attente)


@router.get("/devices", response_model=list[SparkplugDeviceRead])
def lister_devices(db: Session = Depends(get_db)) -> list[SparkplugDeviceRead]:
    devices = db.execute(
        select(SparkplugDevice).order_by(SparkplugDevice.edge_node_id, SparkplugDevice.device_id)
    ).scalars()
    return [_device_read(d) for d in devices]


@router.get("/devices/{device_id}", response_model=SparkplugDeviceRead)
def obtenir_device(device_id: int, db: Session = Depends(get_db)) -> SparkplugDeviceRead:
    return _device_read(_device_ou_404(db, device_id))


@router.patch(
    "/devices/{device_id}",
    response_model=SparkplugDeviceRead,
    dependencies=[Depends(require_admin)],
)
def rattacher_machine(
    device_id: int, payload: SparkplugDeviceBind, db: Session = Depends(get_db)
) -> SparkplugDeviceRead:
    """Rattache l'automate à une machine MES (ou le détache avec `null`)."""
    device = _device_ou_404(db, device_id)
    if payload.machine_id is not None:
        machine = db.get(Machine, payload.machine_id)
        if machine is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine introuvable.")
        autre = db.execute(
            select(SparkplugDevice).where(
                SparkplugDevice.machine_id == machine.id, SparkplugDevice.id != device.id
            )
        ).scalars().first()
        if autre is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{machine.code} est déjà rattachée à l'automate {autre.device_id}.",
            )
    device.machine_id = payload.machine_id
    db.flush()
    db.refresh(device)
    return _device_read(device)


@router.post(
    "/devices/{device_id}/mappings",
    response_model=SparkplugTagMappingRead,
    dependencies=[Depends(require_admin)],
)
def configurer_mapping(
    device_id: int,
    payload: SparkplugTagMappingCreateOrUpdate,
    db: Session = Depends(get_db),
) -> SparkplugTagMappingRead:
    """Crée ou met à jour la règle d'un tag (clé : device + nom du tag)."""
    device = _device_ou_404(db, device_id)
    mapping = db.execute(
        select(SparkplugTagMapping).where(
            SparkplugTagMapping.device_id == device.id,
            SparkplugTagMapping.tag_name == payload.tag_name,
        )
    ).scalar_one_or_none()
    if mapping is None:
        mapping = SparkplugTagMapping(device_id=device.id, tag_name=payload.tag_name)
        db.add(mapping)
    mapping.target_kpi = payload.target_kpi
    mapping.transformation = payload.transformation
    mapping.formula_param = payload.formula_param
    mapping.description = payload.description
    mapping.actif = payload.actif
    db.flush()
    db.refresh(mapping)
    return SparkplugTagMappingRead.model_validate(mapping)


@router.delete(
    "/devices/{device_id}/mappings/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def supprimer_mapping(device_id: int, mapping_id: int, db: Session = Depends(get_db)) -> None:
    mapping = db.get(SparkplugTagMapping, mapping_id)
    if mapping is None or mapping.device_id != device_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Règle introuvable.")
    db.delete(mapping)


@router.post("/rebirth", response_model=RebirthRead, dependencies=[Depends(require_admin)])
def demander_rebirth() -> RebirthRead:
    """Demande à chaque edge node connu de republier ses naissances (état complet)."""
    host = runtime.get_host()
    if host is None or not host.connected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Broker MQTT non connecté.")
    return RebirthRead(edge_nodes=host.request_rebirth_all())
