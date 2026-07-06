"""Schemas du graphe de flux des lignes : nœuds (lignes + articles), liens (ligne->ligne)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ArticleMini(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    designation: str


class LigneNode(BaseModel):
    id: int
    code: str
    designation: str
    actif: bool
    article_ids: list[int]


class LigneLienRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    target_id: int


class FluxRead(BaseModel):
    """Tout ce qu'il faut pour dessiner le canvas : lignes, liens, catalogue d'articles."""

    lignes: list[LigneNode]
    liens: list[LigneLienRead]
    articles: list[ArticleMini]


class LienCreate(BaseModel):
    source_id: int
    target_id: int


class ArticlesSet(BaseModel):
    article_ids: list[int]
