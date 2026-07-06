"""FastAPI application factory and entrypoint."""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    logger.info("startup", app=settings.app_name, environment=settings.environment)
    # Create database tables (idempotent).
    from app.db.session import init_db

    init_db()
    # Best-effort: ensure the Pinecone index exists. Non-fatal if it cannot run
    # (e.g. missing credentials in local dev) so the app can still boot.
    try:
        from app.services.vector_store import ensure_index_exists

        ensure_index_exists()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pinecone_index_check_skipped", error=str(exc))

    # Enregistre la boucle serveur pour les broadcasts depuis les threads
    # (outils agent, superviseur, simulation auto).
    import asyncio

    from app.services.websocket_manager import manager

    manager.set_loop(asyncio.get_running_loop())

    background_tasks: list[asyncio.Task] = []
    if settings.supervisor_enabled:
        from app.services.supervisor_service import boucle_superviseur

        background_tasks.append(asyncio.create_task(boucle_superviseur()))
    if settings.auto_sim_autostart:
        from app.services.auto_simulator import auto_simulator

        auto_simulator.demarrer()

    yield

    from app.services.auto_simulator import auto_simulator

    auto_simulator.arreter()
    for task in background_tasks:
        task.cancel()
    logger.info("shutdown")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request",
                method=request.method,
                path=request.url.path,
                duration_ms=elapsed_ms,
            )
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = request_id
        return response


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    from app.api.routes.console import router as console_router

    app.include_router(console_router)

    from app.websockets.dashboard_ws import register_websocket_routes

    register_websocket_routes(app)

    return app


app = create_app()
