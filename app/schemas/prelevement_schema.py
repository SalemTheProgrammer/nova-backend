"""Schémas Pydantic pour les prélèvements et la libération de lots MP (BPF/DPM)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Unite
from app.models.prelevement import StatutPrelevement


class PrelevementCreateRequest(BaseModel):
    lot_id: int
    quantite_prelevee: Decimal = Field(..., gt=0)
    unite: Unite = Unite.G
    preleveur: str = Field(..., min_length=2)
    zone_prelevement: str = "SAS Prélèvement MP - Flux Laminaire ISO 5"


class PrelevementValiderRequest(BaseModel):
    conforme: bool = True
    analyste: str = Field(..., min_length=2)
    bulletin_analyse_ref: str = Field(..., min_length=2)
    commentaire: Optional[str] = None


class PrelevementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero: str
    lot_matiere_premiere_id: int
    numero_lot: Optional[str] = None
    code_matiere: Optional[str] = None
    nom_matiere: Optional[str] = None
    quantite_prelevee: Decimal
    unite: Unite
    preleveur: str
    zone_prelevement: str
    date_prelevement: datetime
    statut: StatutPrelevement
    date_analyse: Optional[datetime] = None
    analyste: Optional[str] = None
    bulletin_analyse_ref: Optional[str] = None
    commentaire: Optional[str] = None
