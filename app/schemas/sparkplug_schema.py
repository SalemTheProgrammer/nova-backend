"""Schémas Pydantic : hôte Sparkplug B, automates et règles de mappage."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.sparkplug import TagTransformation, TargetKpi


class SparkplugTagMappingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    tag_name: str
    target_kpi: TargetKpi
    transformation: TagTransformation
    formula_param: str | None = None
    description: str | None = None
    actif: bool


class SparkplugTagMappingCreateOrUpdate(BaseModel):
    tag_name: str = Field(..., min_length=1, max_length=100)
    target_kpi: TargetKpi
    transformation: TagTransformation = TagTransformation.DIRECT
    formula_param: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    actif: bool = True


class SparkplugDeviceRead(BaseModel):
    id: int
    name: str
    group_id: str
    edge_node_id: str
    device_id: str
    machine_id: int | None = None
    machine_code: str | None = None
    broker_url: str | None = None
    online: bool
    last_birth_at: datetime | None = None
    last_data_at: datetime | None = None
    available_metrics: dict[str, Any] | None = None
    last_values: dict[str, Any] | None = None
    mappings: list[SparkplugTagMappingRead] = Field(default_factory=list)


class SparkplugDeviceBind(BaseModel):
    """Rattache (ou détache, `null`) un automate à une machine MES."""

    machine_id: int | None


class SparkplugStatusRead(BaseModel):
    enabled: bool
    connected: bool
    broker: str | None
    group_id: str
    host_id: str
    last_error: str | None
    queue_depth: int
    edge_nodes: int
    commandes_en_attente: int


class RebirthRead(BaseModel):
    edge_nodes: int
