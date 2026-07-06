"""Broadcast manager for the /ws/dashboard channel (single process, in-memory)."""
from __future__ import annotations

import asyncio

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the server event loop so sync code (agent tools, background
        threads) can push broadcasts via `broadcast_threadsafe`."""
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)

    def broadcast_threadsafe(self, message: dict) -> None:
        """Schedule a broadcast from a non-async context (executor thread).

        No-op if the loop is not registered yet (e.g. during tests) — broadcasting
        is best-effort and must never break the caller's transaction.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(message), loop)
        except Exception:  # noqa: BLE001
            logger.warning("ws_broadcast_threadsafe_failed")


manager = ConnectionManager()
