"""Pydantic schemas for the chat/agent API."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000, description="User message")
    thread_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Conversation thread id; reuse to keep multi-turn memory.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"message": "What does the onboarding doc say about API keys?"}]
        }
    }


class ChatResponse(BaseModel):
    thread_id: str
    response: str


class IngestDocument(BaseModel):
    content: str = Field(..., min_length=1)
    source: str | None = Field(default=None, description="Optional source identifier")
    metadata: dict[str, str] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    documents: list[IngestDocument] = Field(..., min_length=1, max_length=100)


class IngestResponse(BaseModel):
    ingested: int
    ids: list[str]
