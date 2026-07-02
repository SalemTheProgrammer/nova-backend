"""Pydantic schemas for quality tracking."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.enums import CauseRebut, TypeEvenementQualite


class QualityEventRead(BaseModel):
    id: int
    machine_id: int
    code_machine: str
    ordre_fabrication_id: int | None
    type: TypeEvenementQualite
    quantite: int
    cause: CauseRebut | None
    created_at: datetime


class QualiteResume(BaseModel):
    quantite_bonne: int
    quantite_rejetee: int
    taux_rebut: Decimal
    causes: dict[str, int]
