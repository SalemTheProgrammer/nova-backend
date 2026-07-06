"""Schemas pour les documents indexés et la recherche RAG."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nom: str
    fichier: str
    categorie: str | None
    nb_pages: int
    nb_chunks: int
    statut: str
    date_creation: datetime


class DocumentPassage(BaseModel):
    document_id: int | None
    document_nom: str
    source: str
    page: int | None
    score: float
    citation: str
    # Phrases du chunk qui répondent le mieux à la question (surlignage précis).
    extraits: list[str] = []


class DocumentSearchResult(BaseModel):
    query: str
    passages: list[DocumentPassage]
