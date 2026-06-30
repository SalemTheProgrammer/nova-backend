"""Schemas pour les documents normatifs et la recherche RAG."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NormeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nom: str
    fichier: str
    nb_pages: int
    nb_chunks: int
    statut: str
    date_creation: datetime


class NormePassage(BaseModel):
    norme_id: int | None
    norme_nom: str
    source: str
    page: int | None
    score: float
    citation: str


class NormeSearchResult(BaseModel):
    query: str
    passages: list[NormePassage]
