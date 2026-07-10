"""The reasoning node: calls the LLM with bound tools."""
from __future__ import annotations

from langchain_core.messages import SystemMessage

from app.agent.prompts import SYSTEM_PROMPT, VOICE_PROMPT_ADDENDUM, WHATSAPP_PROMPT_ADDENDUM
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS
from app.services.llm import get_chat_model


def call_model(state: AgentState) -> dict:
    model = get_chat_model().bind_tools(ALL_TOOLS)
    prompt = SYSTEM_PROMPT
    if state.get("mode") == "voix":
        prompt += VOICE_PROMPT_ADDENDUM
    elif state.get("mode") == "whatsapp":
        prompt += WHATSAPP_PROMPT_ADDENDUM
    messages = [SystemMessage(content=prompt), *state["messages"]]
    response = model.invoke(messages)
    return {"messages": [response]}
