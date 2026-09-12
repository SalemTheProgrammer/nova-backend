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
    # Crée/promeut le numéro administrateur (accès total + page d'admin).
    from app.services.auth_service import seed_admin

    seed_admin()
    # Best-effort: ensure the Pinecone index exists. Non-fatal if it cannot run
    # (e.g. missing credentials in local dev) so the app can still boot.
    try:
        from app.services.vector_store import ensure_index_exists

        ensure_index_exists()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pinecone_index_check_skipped", error=str(exc))

    # Checkpointer SQLite persistant : l'historique de conversation (par
    # thread_id) survit au refresh de la page ET aux redémarrages du backend
    # (contrairement au MemorySaver en RAM utilisé auparavant).
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from app.agent.graph import init_graph
    from app.core.paths import chat_history_db_path

    checkpointer_cm = AsyncSqliteSaver.from_conn_string(chat_history_db_path())
    checkpointer = await checkpointer_cm.__aenter__()
    init_graph(checkpointer)

    # Enregistre la boucle serveur pour les broadcasts depuis les threads
    # (outils agent, superviseur, simulation auto).
    import asyncio

    from app.services.websocket_manager import manager

    manager.set_loop(asyncio.get_running_loop())

    background_tasks: list[asyncio.Task] = []
    if settings.supervisor_enabled:
        from app.services.supervisor_service import boucle_superviseur

        background_tasks.append(asyncio.create_task(boucle_superviseur()))
    if settings.auto_bilan_enabled:
        from app.services.scheduled_report_service import boucle_bilan_auto

        background_tasks.append(asyncio.create_task(boucle_bilan_auto()))
    # Envois programmés par l'agent (« dans 5 minutes, envoie le bilan au +216… »).
    from app.services.scheduler_service import boucle_envois_planifies

    background_tasks.append(asyncio.create_task(boucle_envois_planifies()))

    # Hôte Sparkplug B : télémétrie des automates + commandes machine (MQTT).
    from app.protocols.sparkplug_b import runtime

    sparkplug_host = None
    if settings.mqtt_enabled:
        from app.protocols.sparkplug_b.host import SparkplugHost

        sparkplug_host = SparkplugHost(settings)
        runtime.set_host(sparkplug_host)
        sparkplug_host.start()

    yield

    if sparkplug_host is not None:
        runtime.set_host(None)
        await asyncio.to_thread(sparkplug_host.stop)
    for task in background_tasks:
        task.cancel()
    await checkpointer_cm.__aexit__(None, None, None)
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

    from app.websockets.dashboard_ws import register_websocket_routes

    register_websocket_routes(app)

    return app


app = create_app()
