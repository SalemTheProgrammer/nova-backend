"""LangGraph wiring: agent <-> tools loop compiled into a runnable graph."""
from __future__ import annotations

from functools import lru_cache

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


@lru_cache
def get_compiled_graph() -> CompiledStateGraph:
    """Compile once and reuse. MemorySaver gives per-thread conversation memory.

    Swap MemorySaver for a persistent checkpointer (e.g. Postgres) in production
    if conversations must survive restarts / scale across replicas.
    """
    return build_graph().compile(checkpointer=MemorySaver())
