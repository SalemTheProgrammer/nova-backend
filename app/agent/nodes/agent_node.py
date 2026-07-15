"""The reasoning node: calls the LLM with bound tools."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langchain_core.messages import SystemMessage

from app.agent.prompts import SYSTEM_PROMPT, VOICE_PROMPT_ADDENDUM, WHATSAPP_PROMPT_ADDENDUM
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS
from app.services.llm import get_chat_model


def call_model(state: AgentState) -> dict:
    model = get_chat_model().bind_tools(ALL_TOOLS)
    # Tunisia has used UTC+1 year-round since 2009. A fixed offset avoids the
    # optional IANA `tzdata` dependency, which is not bundled on every Windows
    # Python installation used by the demo.
    now = datetime.now(timezone(timedelta(hours=1), name="Africa/Tunis"))
    today = now.date()
    next_monday = today + timedelta(days=(7 - today.weekday()))
    next_friday = next_monday + timedelta(days=4)
    prompt = SYSTEM_PROMPT + (
        "\n\nCONTEXTE TEMPOREL DYNAMIQUE (heure locale de l'usine, Africa/Tunis) :\n"
        f"- Aujourd'hui : {today.isoformat()} ({today.strftime('%A')}).\n"
        f"- Prochaine semaine ouvrée : du {next_monday.isoformat()} au {next_friday.isoformat()}.\n"
        "Utilise ces dates pour résoudre les expressions relatives de l'opérateur.\n"
    )
    if state.get("mode") == "voix":
        prompt += VOICE_PROMPT_ADDENDUM
    elif state.get("mode") == "whatsapp":
        prompt += WHATSAPP_PROMPT_ADDENDUM
    messages = [SystemMessage(content=prompt), *state["messages"]]
    response = model.invoke(messages)
    return {"messages": [response]}
