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
    routes_downtime,
    routes_kpi,
    routes_machines,
    routes_maintenance,
    routes_quality,
    routes_simulator,
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
api_router.include_router(routes_machines.router)
api_router.include_router(routes_simulator.router)
api_router.include_router(routes_downtime.router)
api_router.include_router(routes_quality.router)
api_router.include_router(routes_maintenance.router)
api_router.include_router(routes_kpi.router)
