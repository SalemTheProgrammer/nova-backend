"""`/ws/dashboard` : canal de diffusion (broadcast-only) pour le tableau de bord temps réel.

Les clients (dashboard, page Machines, panneau IA) se connectent et reçoivent les messages
poussés par l'ingestion Sparkplug et les services (`broadcast_service`) via
`websocket_manager.manager`. Le serveur
ne traite pas de commandes entrantes sur ce canal — seule la boucle de réception sert à
détecter la déconnexion.
"""
from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger
from app.services.websocket_manager import manager

logger = get_logger(__name__)


def register_websocket_routes(app: FastAPI) -> None:
    @app.websocket("/ws/dashboard")
    async def dashboard_ws(websocket: WebSocket) -> None:
        await manager.connect(websocket)
        logger.info("ws_connect", client=str(websocket.client))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(websocket)
            logger.info("ws_disconnect", client=str(websocket.client))
