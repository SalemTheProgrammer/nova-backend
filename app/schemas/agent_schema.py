"""Schémas Pydantic pour le superviseur autonome (propositions d'action)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AgentProposalRead(BaseModel):
    id: int
    type: str
    severite: str
    titre: str
    diagnostic: str
    action_libelle: str
    action: dict
    statut: str
    machine_id: int | None
    ordre_fabrication_id: int | None
    resultat: str | None
    created_at: datetime
    decided_at: datetime | None
