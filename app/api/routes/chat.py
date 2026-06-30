"""Chat (agent) and document-ingestion endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agent.runner import run_agent
from app.core.security import require_api_key
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    IngestRequest,
    IngestResponse,
)
from app.services.vector_store import add_documents
from langchain_core.documents import Document

router = APIRouter(tags=["agent"], dependencies=[Depends(require_api_key)])


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    response = await run_agent(payload.message, thread_id=payload.thread_id)
    return ChatResponse(thread_id=payload.thread_id, response=response)


@router.post("/ingest", response_model=IngestResponse)
async def ingest(payload: IngestRequest) -> IngestResponse:
    documents = [
        Document(
            page_content=doc.content,
            metadata={**doc.metadata, **({"source": doc.source} if doc.source else {})},
        )
        for doc in payload.documents
    ]
    ids = add_documents(documents)
    return IngestResponse(ingested=len(ids), ids=ids)
