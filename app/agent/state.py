"""Shared graph state definition for the agent."""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """State threaded through the LangGraph nodes.

    `messages` accumulates across nodes via the add_messages reducer.
    `mode` : "texte" (défaut) ou "voix" — en voix, la réponse est lue à voix
    haute et le prompt système impose des réponses courtes au registre parlé.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    mode: str
