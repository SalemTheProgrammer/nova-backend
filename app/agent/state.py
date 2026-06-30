"""Shared graph state definition for the agent."""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """State threaded through the LangGraph nodes.

    `messages` accumulates across nodes via the add_messages reducer.
    """

    messages: Annotated[list[BaseMessage], add_messages]
