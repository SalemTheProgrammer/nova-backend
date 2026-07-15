"""Chat (agent) endpoints."""
from __future__ import annotations

import orjson
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.agent.runner import get_thread_history, run_agent, stream_agent
from app.core.security import require_api_key
from app.schemas.chat import ChatHistoryResponse, ChatRequest, ChatResponse

router = APIRouter(tags=["agent"], dependencies=[Depends(require_api_key)])


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    response = await run_agent(payload.message, thread_id=payload.thread_id, mode=payload.mode)
    return ChatResponse(thread_id=payload.thread_id, response=response)


@router.get("/chat/{thread_id}/history", response_model=ChatHistoryResponse)
async def chat_history(thread_id: str) -> ChatHistoryResponse:
    """Historique des tours d'un thread, pour réhydrater le panneau de chat
    après un refresh de page (voir `useAgentChat.ts`)."""
    turns = await get_thread_history(thread_id)
    return ChatHistoryResponse(thread_id=thread_id, turns=turns)


def _sse(event: dict) -> str:
    return f"data: {orjson.dumps(event, default=str).decode()}\n\n"


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    """Stream the agent run as Server-Sent Events (tokens + tool steps + artifacts)."""

    async def generate() -> AsyncIterator[str]:
        async for event in stream_agent(
            payload.message, thread_id=payload.thread_id, mode=payload.mode
        ):
            yield _sse(event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
