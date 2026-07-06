"""High-level entrypoint to run the agent for an API request."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import get_compiled_graph
from app.agent.tools.navigation import PAGES, TOOL_PAGE_MAP
from app.core.config import get_settings
from app.core.exceptions import AgentError
from app.core.logging import get_logger

logger = get_logger(__name__)


async def run_agent(message: str, *, thread_id: str, mode: str = "texte") -> str:
    """Run the agent for a single user message within a conversation thread.

    `thread_id` keys the checkpointer so multi-turn conversations retain memory.
    """
    settings = get_settings()
    graph = get_compiled_graph()
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": settings.agent_recursion_limit,
    }
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


async def stream_agent(message: str, *, thread_id: str, mode: str = "texte") -> AsyncIterator[dict]:
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
    settings = get_settings()
    graph = get_compiled_graph()
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": settings.agent_recursion_limit,
    }
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
