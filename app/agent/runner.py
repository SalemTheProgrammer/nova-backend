"""High-level entrypoint to run the agent for an API request."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import get_compiled_graph
from app.core.config import get_settings
from app.core.exceptions import AgentError
from app.core.logging import get_logger

logger = get_logger(__name__)


async def run_agent(message: str, *, thread_id: str) -> str:
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
            {"messages": [HumanMessage(content=message)]},
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
