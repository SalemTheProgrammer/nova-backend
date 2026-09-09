"""Schémas Pydantic pour Sparkplug B MQTT et configuration des KPIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.sparkplug import TagTransformation, TargetKpi


class SparkplugTagMappingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    tag_name: str
    target_kpi: TargetKpi
    transformation: TagTransformation
    formula_param: Optional[str] = None
    description: Optional[str] = None
    actif: bool


class SparkplugTagMappingCreateOrUpdate(BaseModel):
    tag_name: str
    target_kpi: TargetKpi
    transformation: TagTransformation = TagTransformation.DIRECT
    formula_param: Optional[str] = None
    description: Optional[str] = None
    actif: bool = True


class SparkplugDeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    group_id: str
    edge_node_id: str
    device_id: str
    machine_id: Optional[int] = None
    online: bool
    last_birth_at: Optional[datetime] = None
    last_data_at: Optional[datetime] = None
    available_metrics: Optional[dict[str, Any]] = None
    mappings: list[SparkplugTagMappingRead] = Field(default_factory=list)


class SparkplugCardSwipeRequest(BaseModel):
    card_code: str = Field(..., description="Code carte RFID (ex: CARTE_REGLAGE, CARTE_PANNE_MECA, CARTE_REPRISE)")


class SparkplugTickRequest(BaseModel):
    good_increment: int = 2
    reject_increment: int = 0
    cadence_cpm: float = 48.0
    temperature: float = 24.0
    operator_card: Optional[str] = None
