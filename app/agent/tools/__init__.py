"""Tool registry for the agent."""
from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agent.tools.documents import lister_documents_disponibles, rechercher_documents
from app.agent.tools.navigation import NAVIGATION_TOOLS
from app.agent.tools.notify import NOTIFY_TOOLS
from app.agent.tools.scheduling import SCHEDULING_TOOLS
from app.agent.tools.tunicotex import TUNICOTEX_TOOLS

DOCUMENT_TOOLS: list[BaseTool] = [rechercher_documents, lister_documents_disponibles]

ALL_TOOLS: list[BaseTool] = [
    *DOCUMENT_TOOLS,
    *TUNICOTEX_TOOLS,
    *NOTIFY_TOOLS,
    *SCHEDULING_TOOLS,
    *NAVIGATION_TOOLS,
]

# Regroupement des outils par catégorie (libellés français) — sert à la page
# d'administration : cocher, groupe par groupe, les outils autorisés par numéro.
_GROUPES: list[tuple[str, list[BaseTool]]] = [
    ("Documents", DOCUMENT_TOOLS),
    ("Atelier Tunicotex", TUNICOTEX_TOOLS),
    ("Notifications", NOTIFY_TOOLS),
    ("Planification d'envois", SCHEDULING_TOOLS),
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

# Outils SANS effet sur l'atelier : ceux qui n'exigent pas de `confirmation`.
# C'est le contrat des outils qui modifient quelque chose ou envoient un message
# (voir `confirmation_gate`), donc le critère se maintient tout seul : un nouvel
# outil d'action, qui suivra ce contrat, sera exclu d'office. Sert de périmètre
# à la discussion web (canal en lecture seule, voir `routes/chat.py`).
READONLY_TOOL_NAMES: list[str] = sorted(
    outil.name for outil in ALL_TOOLS if "confirmation" not in outil.args
)

__all__ = [
    "ALL_TOOLS",
    "DOCUMENT_TOOLS",
    "TOOL_CATALOG",
    "ALL_TOOL_NAMES",
    "READONLY_TOOL_NAMES",
    "rechercher_documents",
    "lister_documents_disponibles",
    "TUNICOTEX_TOOLS",
    "NOTIFY_TOOLS",
    "SCHEDULING_TOOLS",
    "NAVIGATION_TOOLS",
]
