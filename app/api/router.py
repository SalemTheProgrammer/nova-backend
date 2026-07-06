"""Aggregate API router."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    articles,
    chat,
    documents,
    fournisseurs,
    health,
    lignes,
    matieres,
    ordres,
    routes_ligne_flux,
    routes_agent,
    routes_downtime,
    routes_kpi,
    routes_machines,
    routes_maintenance,
    routes_quality,
    routes_simulator,
    routes_voice,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(articles.router)
api_router.include_router(matieres.router)
api_router.include_router(fournisseurs.router)
api_router.include_router(lignes.router)
api_router.include_router(routes_ligne_flux.router)
api_router.include_router(ordres.router)
api_router.include_router(documents.router)
api_router.include_router(routes_machines.router)
api_router.include_router(routes_simulator.router)
api_router.include_router(routes_downtime.router)
api_router.include_router(routes_quality.router)
api_router.include_router(routes_maintenance.router)
api_router.include_router(routes_kpi.router)
api_router.include_router(routes_agent.router)
api_router.include_router(routes_voice.router)
