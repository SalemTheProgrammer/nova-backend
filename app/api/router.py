"""Aggregate API router."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    articles,
    chat,
    fournisseurs,
    health,
    lignes,
    matieres,
    normes,
    ordres,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(articles.router)
api_router.include_router(matieres.router)
api_router.include_router(fournisseurs.router)
api_router.include_router(lignes.router)
api_router.include_router(ordres.router)
api_router.include_router(normes.router)
