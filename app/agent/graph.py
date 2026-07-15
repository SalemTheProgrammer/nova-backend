"""LangGraph wiring: agent <-> tools loop compiled into a runnable graph."""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.nodes.agent_node import call_model
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(ALL_TOOLS))

    graph.add_edge(START, "agent")
    # tools_condition routes to "tools" when the LLM emitted tool calls, else END.
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph


_compiled_graph: CompiledStateGraph | None = None


def init_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    """Compile the graph with the given checkpointer. Called once from the
    FastAPI lifespan (see `main.py`) with a persistent `AsyncSqliteSaver` so
    conversation history (thread_id -> messages) survives page refreshes and
    backend restarts, not just the lifetime of the process.
    """
    global _compiled_graph
    _compiled_graph = build_graph().compile(checkpointer=checkpointer)
    return _compiled_graph


def get_compiled_graph() -> CompiledStateGraph:
    """Return the graph compiled by `init_graph` during app startup.

    Falls back to an in-memory (non-persistent) checkpointer if called outside
    the normal FastAPI lifespan (e.g. ad-hoc scripts) so callers still work,
    just without cross-restart durability.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph().compile(checkpointer=MemorySaver())
    return _compiled_graph
