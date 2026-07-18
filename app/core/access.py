"""Contrôle d'accès par catégorie d'outil pour les endpoints REST de données.

`outils_autorises` gate déjà l'agent IA (voir `agent/graph.py` : bind_tools +
garde-fou d'exécution) — mais un numéro restreint au chat pouvait quand même
tout voir en naviguant dans l'app, car les endpoints REST que le frontend
appelle directement pour peupler ses pages (ex. `GET /articles`) n'étaient
protégés que par la clé API partagée (`require_api_key`, identique pour tout
le monde), jamais par l'identité de l'utilisateur connecté. Ce module applique
la même restriction que le chat à ces endpoints, en réutilisant le même
catalogue de catégories que la page d'administration.
"""
from __future__ import annotations

from fastapi import Depends

from app.agent.tools import TOOL_CATALOG
from app.core.security import ForbiddenError, get_current_user
from app.models.utilisateur import Utilisateur

_CATEGORY_TOOLS: dict[str, set[str]] = {}
for _outil in TOOL_CATALOG:
    _CATEGORY_TOOLS.setdefault(_outil["categorie"], set()).add(_outil["name"])


def require_category(*categories: str):
    """Dépendance de routeur : admin, ou utilisateur ayant au moins un outil
    autorisé parmi les catégories données (OU entre catégories).

    Usage : `dependencies=[Depends(require_api_key), Depends(require_category("Fabrication"))]`.
    """
    outils_requis: set[str] = set()
    for categorie in categories:
        outils_requis |= _CATEGORY_TOOLS.get(categorie, set())

    async def _dependance(user: Utilisateur = Depends(get_current_user)) -> Utilisateur:
        if user.is_admin:
            return user
        if outils_requis & set(user.outils_autorises or []):
            return user
        raise ForbiddenError(
            "Accès réservé : votre compte n'a pas les outils nécessaires pour cette section."
        )

    return _dependance
