"""LangGraph wiring: agent <-> tools loop compiled into a runnable graph.

ATTENTION : PAS de `from __future__ import annotations` dans ce module (ni dans
agent_node.py). Cet import transforme les annotations en chaînes, et l'
inspection de signature de LangGraph ne reconnaît alors plus le paramètre
`config: RunnableConfig | None` des nœuds : le config n'est PAS injecté
(config=None, avec seulement un UserWarning au démarrage), donc
`outils_autorises` n'arrive jamais aux nœuds et TOUTE la restriction d'outils
par utilisateur est silencieusement désactivée — chaque numéro a tous les
outils. Bug découvert en production le 2026-07-18.
"""
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.nodes.agent_node import REFUS_OUTIL, call_model
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS

_tool_node = ToolNode(ALL_TOOLS)


def guarded_tools(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """Exécute les appels d'outils du dernier tour en filtrant le périmètre.

    Deuxième filet APRÈS `bind_tools` (agent_node) : même si un modèle émettait
    un appel hors périmètre, il est ici refusé (ToolMessage en français) au lieu
    d'être exécuté. `outils_autorises=None` (admin) → tous les outils passent.
    """
    configurable = (config or {}).get("configurable") or {}
    allowed = configurable.get("outils_autorises")
    messages = state["messages"]
    dernier = messages[-1] if messages else None

    if allowed is None or not isinstance(dernier, AIMessage) or not dernier.tool_calls:
        return _tool_node.invoke(state, config)

    autorises = set(allowed)
    permis = [tc for tc in dernier.tool_calls if tc["name"] in autorises]
    refuses = [tc for tc in dernier.tool_calls if tc["name"] not in autorises]

    sortie: list = []
    if permis:
        # ToolNode lit les tool_calls du dernier message : on lui présente une
        # copie ne contenant que les appels permis (les ids sont préservés, donc
        # les ToolMessages produits correspondent bien au message d'origine).
        ai_permis = AIMessage(content=dernier.content, tool_calls=permis, id=dernier.id)
        sous_etat = {**state, "messages": [*messages[:-1], ai_permis]}
        resultat = _tool_node.invoke(sous_etat, config)
        sortie.extend(resultat["messages"])
    for tc in refuses:
        sortie.append(ToolMessage(content=REFUS_OUTIL, tool_call_id=tc["id"], name=tc["name"]))
    return {"messages": sortie}


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", guarded_tools)

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
