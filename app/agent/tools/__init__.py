"""Tool registry for the agent."""
from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agent.tools.actions import ACTION_TOOLS
from app.agent.tools.charts import CHART_TOOLS
from app.agent.tools.documents import lister_documents_disponibles, rechercher_documents
from app.agent.tools.manufacturing import MANUFACTURING_TOOLS
from app.agent.tools.mes import MES_TOOLS
from app.agent.tools.navigation import NAVIGATION_TOOLS
from app.agent.tools.notify import NOTIFY_TOOLS
from app.agent.tools.planning import PLANNING_TOOLS
from app.agent.tools.scheduling import SCHEDULING_TOOLS
from app.agent.tools.supervision import SUPERVISION_TOOLS

DOCUMENT_TOOLS: list[BaseTool] = [rechercher_documents, lister_documents_disponibles]

ALL_TOOLS: list[BaseTool] = [
    *DOCUMENT_TOOLS,
    *MANUFACTURING_TOOLS,
    *MES_TOOLS,
    *ACTION_TOOLS,
    *SUPERVISION_TOOLS,
    *CHART_TOOLS,
    *NOTIFY_TOOLS,
    *SCHEDULING_TOOLS,
    *PLANNING_TOOLS,
    *NAVIGATION_TOOLS,
]

# Regroupement des outils par catégorie (libellés français) — sert à la page
# d'administration : cocher, groupe par groupe, les outils autorisés par numéro.
_GROUPES: list[tuple[str, list[BaseTool]]] = [
    ("Documents", DOCUMENT_TOOLS),
    ("Fabrication", MANUFACTURING_TOOLS),
    ("Supervision / MES", MES_TOOLS),
    ("Actions machine", ACTION_TOOLS),
    ("Décisions du superviseur", SUPERVISION_TOOLS),
    ("Graphiques", CHART_TOOLS),
    ("Notifications", NOTIFY_TOOLS),
    ("Planification d'envois", SCHEDULING_TOOLS),
    ("Ordonnancement", PLANNING_TOOLS),
    ("Navigation", NAVIGATION_TOOLS),
]

# Catalogue plat {name, categorie, description} consommé par le frontend admin.
TOOL_CATALOG: list[dict[str, str]] = [
    {
        "name": outil.name,
        "categorie": categorie,
        "description": (outil.description or "").strip().split("\n")[0][:200],
    }
    for categorie, outils in _GROUPES
    for outil in outils
]

# Ensemble de tous les noms d'outils connus (validation côté admin).
ALL_TOOL_NAMES: set[str] = {outil.name for outil in ALL_TOOLS}

__all__ = [
    "ALL_TOOLS",
    "DOCUMENT_TOOLS",
    "TOOL_CATALOG",
    "ALL_TOOL_NAMES",
    "rechercher_documents",
    "lister_documents_disponibles",
    "MANUFACTURING_TOOLS",
    "MES_TOOLS",
    "ACTION_TOOLS",
    "SUPERVISION_TOOLS",
    "CHART_TOOLS",
    "NOTIFY_TOOLS",
    "SCHEDULING_TOOLS",
    "PLANNING_TOOLS",
    "NAVIGATION_TOOLS",
]
