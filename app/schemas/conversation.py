"""Schémas de la page d'administration « Conversations » (lecture seule)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.chat import AgentTurn


class ConversationResume(BaseModel):
    thread_id: str
    canal: str
    utilisateur_id: int | None
    utilisateur_nom: str
    telephone: str
    apercu: str
    date_creation: datetime
    date_maj: datetime


class ConversationDetail(BaseModel):
    conversation: ConversationResume
    turns: list[AgentTurn]
