"""High-level entrypoint to run the agent for an API request."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.graph import get_compiled_graph
from app.agent.tools.navigation import PAGES, TOOL_PAGE_MAP
from app.core.config import get_settings
from app.core.exceptions import AgentError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _build_config(thread_id: str, outils_autorises: list[str] | None) -> dict:
    """Config d'exécution du graphe : thread, invocation, et périmètre d'outils.

    `outils_autorises=None` = tous les outils (admin / appel interne) ; une liste
    restreint le modèle (bind_tools) et le garde-fou d'exécution à ces outils.
    """
    settings = get_settings()
    return {
        # `invocation_id` change à chaque appel : c'est ce qui permet au garde-fou
        # de confirmation (voir `agent/tools/confirmation_gate.py`) de distinguer
        # « le modèle se re-confirme lui-même dans la même boucle » d'un vrai
        # aller-retour avec l'opérateur (message suivant = invocation différente).
        "configurable": {
            "thread_id": thread_id,
            "invocation_id": str(uuid.uuid4()),
            "outils_autorises": outils_autorises,
        },
        "recursion_limit": settings.agent_recursion_limit,
    }


async def run_agent(
    message: str,
    *,
    thread_id: str,
    mode: str = "texte",
    outils_autorises: list[str] | None = None,
) -> str:
    """Run the agent for a single user message within a conversation thread.

    `thread_id` keys the checkpointer so multi-turn conversations retain memory.
    """
    graph = get_compiled_graph()
    config = _build_config(thread_id, outils_autorises)
    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=message)], "mode": mode},
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent_run_failed", thread_id=thread_id)
        raise AgentError("Agent execution failed") from exc

    messages = result["messages"]
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    raise AgentError("Agent produced no response")


async def run_agent_avec_artifacts(
    message: str,
    *,
    thread_id: str,
    mode: str = "texte",
    outils_autorises: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """Comme `run_agent`, mais renvoie aussi les artifacts produits PENDANT CE
    TOUR (graphiques, jauges…) pour les canaux qui doivent les rendre eux-mêmes
    — WhatsApp les transforme en images PNG.

    Seuls les ToolMessages situés APRÈS le dernier HumanMessage comptent : le
    checkpointer rejoue tout l'historique du thread, on ne veut pas renvoyer
    les graphiques des tours précédents.
    """
    graph = get_compiled_graph()
    config = _build_config(thread_id, outils_autorises)
    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=message)], "mode": mode},
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent_run_failed", thread_id=thread_id)
        raise AgentError("Agent execution failed") from exc

    messages = result["messages"]
    dernier_humain = 0
    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            dernier_humain = i
    artifacts = [
        msg.artifact
        for msg in messages[dernier_humain:]
        if isinstance(msg, ToolMessage) and isinstance(getattr(msg, "artifact", None), dict)
    ]

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            texte = msg.content if isinstance(msg.content, str) else str(msg.content)
            return texte, artifacts
    raise AgentError("Agent produced no response")


async def get_thread_history(thread_id: str) -> list[dict]:
    """Reconstruit les tours UI (voir `AgentTurn`/`AgentSegment` côté frontend)
    depuis l'historique de messages du checkpointer pour `thread_id`, afin que
    le panneau de chat puisse se réhydrater après un refresh de page.
    """
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": thread_id}}
    state = await graph.aget_state(config)
    messages = state.values.get("messages", []) if state.values else []
    return _messages_to_turns(messages)


def _messages_to_turns(messages: list) -> list[dict]:
    turns: list[dict] = []
    assistant_turn: dict | None = None
    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            turns.append(
                {"id": f"h-{i}", "role": "user", "segments": [{"type": "text", "content": content}]}
            )
            assistant_turn = {"id": f"a-{i}", "role": "assistant", "segments": []}
            turns.append(assistant_turn)
        elif isinstance(msg, AIMessage):
            text = msg.content if isinstance(msg.content, str) else ""
            if not text:
                continue
            if assistant_turn is None:
                assistant_turn = {"id": f"a-{i}", "role": "assistant", "segments": []}
                turns.append(assistant_turn)
            segments = assistant_turn["segments"]
            if segments and segments[-1]["type"] == "text":
                segments[-1]["content"] += text
            else:
                segments.append({"type": "text", "content": text})
        elif isinstance(msg, ToolMessage):
            if assistant_turn is None:
                assistant_turn = {"id": f"a-{i}", "role": "assistant", "segments": []}
                turns.append(assistant_turn)
            output = msg.content if isinstance(msg.content, str) else str(msg.content)
            artifact = getattr(msg, "artifact", None)
            assistant_turn["segments"].append(
                {
                    "type": "tool",
                    "id": msg.tool_call_id or f"tool-{i}",
                    "name": msg.name or "tool",
                    "status": "done",
                    "output": output,
                    "artifact": artifact if isinstance(artifact, dict) else None,
                }
            )
    return turns


def _chunk_text(chunk: Any) -> str:
    """Extract plain text from a streamed chat-model chunk (str or content blocks)."""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


async def stream_agent(
    message: str,
    *,
    thread_id: str,
    mode: str = "texte",
    outils_autorises: list[str] | None = None,
) -> AsyncIterator[dict]:
    """Run the agent and yield UI-oriented events (tokens, tool steps, artifacts).

    Event types:
      - turn_start                       : the LLM begins a reasoning turn
      - token {content}                  : streamed text of the final/intermediate answer
      - tool_start {id, name, input}     : a tool (specialised sub-agent) is invoked
      - tool_end {id, name, output, artifact} : the tool finished; artifact is
        structured data for rich rendering (may be None). A synthetic tool_end
        named "aller_a_la_page" may follow a read/action tool automatically —
        see the module-level note below.
      - done {thread_id, response}       : run finished, full final text included
      - error {message}                  : run failed

    Navigation reliability note: the LLM is instructed to call `aller_a_la_page`
    itself, but small/fast models don't always comply. `TOOL_PAGE_MAP` gives a
    deterministic fallback — after a mapped tool actually runs, a synthetic
    navigation event is emitted so the UI redirects reliably regardless of the
    model's own tool choice. Each page fires at most once per run.
    """
    graph = get_compiled_graph()
    config = _build_config(thread_id, outils_autorises)
    final_text = ""
    pages_visitees: set[str] = set()
    try:
        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=message)], "mode": mode},
            config=config,
            version="v2",
        ):
            kind = event["event"]
            if kind == "on_chat_model_start":
                final_text = ""
                yield {"type": "turn_start"}
            elif kind == "on_chat_model_stream":
                text = _chunk_text(event["data"].get("chunk"))
                if text:
                    final_text += text
                    yield {"type": "token", "content": text}
            elif kind == "on_tool_start":
                yield {
                    "type": "tool_start",
                    "id": event["run_id"],
                    "name": event["name"],
                    "input": event["data"].get("input"),
                }
            elif kind == "on_tool_end":
                tool_name = event["name"]
                output = event["data"].get("output")
                content = getattr(output, "content", None)
                if content is None and output is not None:
                    content = str(output)
                artifact = getattr(output, "artifact", None)
                yield {
                    "type": "tool_end",
                    "id": event["run_id"],
                    "name": tool_name,
                    "output": content or "",
                    "artifact": artifact,
                }

                if isinstance(artifact, dict) and artifact.get("kind") == "navigation":
                    pages_visitees.add(str(artifact.get("page")))
                else:
                    page = TOOL_PAGE_MAP.get(tool_name)
                    if page is not None and page not in pages_visitees:
                        pages_visitees.add(page)
                        label = PAGES[page]
                        nav_id = f"nav-{uuid.uuid4()}"
                        # tool_start first: the frontend only turns an existing
                        # "running" segment into "done", it never creates one
                        # from tool_end alone.
                        yield {
                            "type": "tool_start",
                            "id": nav_id,
                            "name": "aller_a_la_page",
                            "input": {"page": page},
                        }
                        yield {
                            "type": "tool_end",
                            "id": nav_id,
                            "name": "aller_a_la_page",
                            "output": f"📍 Page ouverte : {label}",
                            "artifact": {
                                "kind": "navigation",
                                "page": page,
                                "label": label,
                                "raison": None,
                            },
                        }
    except Exception:  # noqa: BLE001
        logger.exception("agent_stream_failed", thread_id=thread_id)
        yield {"type": "error", "message": "L'agent a rencontré une erreur. Réessayez."}
        return
    yield {"type": "done", "thread_id": thread_id, "response": final_text}
