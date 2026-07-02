"""Pydantic schemas for machines and their live SCADA state."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import CauseArret, StatutMachine


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MachineBase(BaseModel):
    code: str
    nom: str
    ligne_production_id: int
    temps_cycle_cible_s: Decimal | None = None


class MachineCreate(MachineBase):
    pass


class MachineUpdate(BaseModel):
    nom: str | None = None
    ligne_production_id: int | None = None
    temps_cycle_cible_s: Decimal | None = None
    actif: bool | None = None


class DowntimeActifRead(BaseModel):
    id: int
    cause: CauseArret
    operator_comment: str | None
    start_time: datetime


class MachineRead(_ORM, MachineBase):
    id: int
    statut: StatutMachine
    ordre_fabrication_id: int | None
    numero_of_actif: str | None = None
    temps_cycle_actuel_s: Decimal | None
    quantite_produite: int
    quantite_bonne: int
    quantite_rejetee: int
    dernier_evenement_at: datetime | None
    downtime_actif: DowntimeActifRead | None = None
    trs: Decimal | None = None
    tq: Decimal | None = None
    tp: Decimal | None = None
    do: Decimal | None = None


class MachineEventRead(BaseModel):
    id: int
    machine_id: int
    ordre_fabrication_id: int | None
    type: str
    payload: dict
    created_at: datetime
