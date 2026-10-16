"""Aggregate API router."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    admin_users,
    audit,
    auth,
    chat,
    documents,
    health,
    routes_voice,
    whatsapp,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin_users.router)
api_router.include_router(audit.router)
api_router.include_router(chat.router)
api_router.include_router(documents.router)
api_router.include_router(routes_voice.router)
api_router.include_router(whatsapp.public_router)
api_router.include_router(whatsapp.router)
