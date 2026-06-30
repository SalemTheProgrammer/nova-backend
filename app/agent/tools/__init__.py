"""Tool registry for the agent."""
from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agent.tools.manufacturing import MANUFACTURING_TOOLS
from app.agent.tools.normes import rechercher_normes
from app.agent.tools.retrieval import search_knowledge_base

ALL_TOOLS: list[BaseTool] = [
    search_knowledge_base,
    rechercher_normes,
    *MANUFACTURING_TOOLS,
]

__all__ = [
    "ALL_TOOLS",
    "search_knowledge_base",
    "rechercher_normes",
    "MANUFACTURING_TOOLS",
]
