"""Tool registry for the agent."""
from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agent.tools.actions import ACTION_TOOLS
from app.agent.tools.charts import CHART_TOOLS
from app.agent.tools.documents import rechercher_documents
from app.agent.tools.manufacturing import MANUFACTURING_TOOLS
from app.agent.tools.mes import MES_TOOLS
from app.agent.tools.navigation import NAVIGATION_TOOLS
from app.agent.tools.notify import NOTIFY_TOOLS
from app.agent.tools.planning import PLANNING_TOOLS
from app.agent.tools.twin import TWIN_TOOLS

ALL_TOOLS: list[BaseTool] = [
    rechercher_documents,
    *MANUFACTURING_TOOLS,
    *MES_TOOLS,
    *ACTION_TOOLS,
    *CHART_TOOLS,
    *NOTIFY_TOOLS,
    *PLANNING_TOOLS,
    *TWIN_TOOLS,
    *NAVIGATION_TOOLS,
]

__all__ = [
    "ALL_TOOLS",
    "rechercher_documents",
    "MANUFACTURING_TOOLS",
    "MES_TOOLS",
    "ACTION_TOOLS",
    "CHART_TOOLS",
    "NOTIFY_TOOLS",
    "PLANNING_TOOLS",
    "TWIN_TOOLS",
    "NAVIGATION_TOOLS",
]
