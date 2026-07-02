"""Request bodies for each Simulateur Machine action."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import CauseArret, CauseRebut, TypeMaintenance


class StartMachineRequest(BaseModel):
    ordre_fabrication_id: int | None = None


class AlarmRequest(BaseModel):
    message: str | None = None


class CycleTimeRequest(BaseModel):
    temps_cycle_s: float = Field(gt=0)


class GoodUnitRequest(BaseModel):
    quantite: int = Field(default=1, gt=0)


class ScrapUnitRequest(BaseModel):
    quantite: int = Field(default=1, gt=0)
    cause: CauseRebut = CauseRebut.AUTRE


class DowntimeStartRequest(BaseModel):
    cause: CauseArret = CauseArret.AUTRE
    comment: str | None = None


class DowntimeResolveRequest(BaseModel):
    comment: str | None = None


class MaintenanceStartRequest(BaseModel):
    type: TypeMaintenance = TypeMaintenance.PREVENTIVE
    description: str | None = None


class MaintenanceEndRequest(BaseModel):
    prochaine_maintenance: str | None = None  # ISO date (AAAA-MM-JJ)


class SensorTagRequest(BaseModel):
    tag: str
    valeur: float | str
