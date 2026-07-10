"""Outil agent d'ordonnancement du backlog d'OF (lecture seule).

Même pattern que `mes.py` : session propre, aucune modification, texte français
lisible par l'opérateur + artifact structuré pour l'interface.
"""
from __future__ import annotations

from langchain_core.tools import tool

from app.db.session import session_scope
from app.services import planning_service


@tool(response_format="content_and_artifact")
def optimiser_planning() -> tuple[str, dict | None]:
    """Ordonnance le backlog d'OF ouverts (planifiés + en cours) sur les machines.

    Trie par échéance la plus proche, affecte chaque OF à la machine qui se
    libère le plus tôt, projette les fins estimées et signale les retards
    prévisionnels. Ne modifie RIEN : c'est une recommandation de séquencement.
    """
    with session_scope() as db:
        return planning_service.optimiser_backlog(db)


PLANNING_TOOLS = [optimiser_planning]
