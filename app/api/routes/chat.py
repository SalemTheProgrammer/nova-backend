"""Chat (agent) endpoints."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import orjson
from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from app.agent.runner import (
    delete_thread,
    enregistrer_accueil,
    get_thread_history,
    run_agent,
    stream_agent,
)
from app.core.security import get_current_user
from app.db.session import session_scope
from app.models.utilisateur import Utilisateur
from app.schemas.chat import (
    ChatAccueilRequest,
    ChatAccueilResponse,
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
)
from app.services import ai_agent_service, auth_service

router = APIRouter(tags=["agent"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest, user: Utilisateur = Depends(get_current_user)
) -> ChatResponse:
    response = await run_agent(
        payload.message,
        thread_id=payload.thread_id,
        mode=payload.mode,
        outils_autorises=auth_service.outils_pour(user),
    )
    return ChatResponse(thread_id=payload.thread_id, response=response)


@router.get("/chat/{thread_id}/history", response_model=ChatHistoryResponse)
async def chat_history(
    thread_id: str, user: Utilisateur = Depends(get_current_user)
) -> ChatHistoryResponse:
    """Historique des tours d'un thread, pour réhydrater le panneau de chat
    après un refresh de page (voir `useAgentChat.ts`)."""
    turns = await get_thread_history(thread_id)
    return ChatHistoryResponse(thread_id=thread_id, turns=turns)


@router.delete("/chat/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def chat_clear(thread_id: str, user: Utilisateur = Depends(get_current_user)) -> None:
    """Efface définitivement l'historique d'un thread (bouton « effacer la
    conversation » du panneau de chat)."""
    await delete_thread(thread_id)


def _accueil_depuis_atelier() -> list[str]:
    with session_scope() as db:
        return ai_agent_service.generer_accueil(db)


@router.post("/chat/accueil", response_model=ChatAccueilResponse)
async def chat_accueil(
    payload: ChatAccueilRequest, user: Utilisateur = Depends(get_current_user)
) -> ChatAccueilResponse:
    """Message d'accueil de Nova pour une conversation vide (salutation, constats
    atelier, décisions en attente). Enregistré comme premier message du thread :
    l'agent sait ce qu'il a dit si l'opérateur y répond."""
    thread_id = payload.thread_id or str(uuid.uuid4())
    messages = await asyncio.to_thread(_accueil_depuis_atelier)
    await enregistrer_accueil(thread_id, messages)
    return ChatAccueilResponse(thread_id=thread_id, messages=messages)


def _sse(event: dict) -> str:
    return f"data: {orjson.dumps(event, default=str).decode()}\n\n"


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest, user: Utilisateur = Depends(get_current_user)
) -> StreamingResponse:
    """Stream the agent run as Server-Sent Events (tokens + tool steps + artifacts)."""
    outils_autorises = auth_service.outils_pour(user)

    async def generate() -> AsyncIterator[str]:
        async for event in stream_agent(
            payload.message,
            thread_id=payload.thread_id,
            mode=payload.mode,
            outils_autorises=outils_autorises,
        ):
            yield _sse(event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
