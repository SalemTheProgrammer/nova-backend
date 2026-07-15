"""Pydantic schemas for downtime tracking."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.enums import CauseArret


class DowntimeRead(BaseModel):
    id: int
    machine_id: int
    code_machine: str
    ordre_fabrication_id: int | None
    cause: CauseArret
    operator_comment: str | None
    start_time: datetime
    end_time: datetime | None
    duree_s: Decimal | None


class DowntimePage(BaseModel):
    items: list[DowntimeRead]
    total: int
    page: int
    page_size: int
