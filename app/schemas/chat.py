"""Pydantic schemas for the chat/agent API."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000, description="User message")
    thread_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Conversation thread id; reuse to keep multi-turn memory.",
    )
    mode: Literal["texte", "voix"] = Field(
        default="texte",
        description="'voix' quand la réponse sera lue à voix haute : l'agent répond court, registre parlé.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"message": "What does the onboarding doc say about API keys?"}]
        }
    }


class ChatResponse(BaseModel):
    thread_id: str
    response: str
