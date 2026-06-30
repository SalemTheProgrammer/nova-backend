"""The reasoning node: calls the LLM with bound tools."""
from __future__ import annotations

from langchain_core.messages import SystemMessage

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS
from app.services.llm import get_chat_model


def call_model(state: AgentState) -> dict:
    model = get_chat_model().bind_tools(ALL_TOOLS)
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = model.invoke(messages)
    return {"messages": [response]}
