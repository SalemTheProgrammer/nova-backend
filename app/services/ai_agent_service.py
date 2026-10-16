"""Message d'accueil de Nova (sans appel LLM).

Version provisoire : un accueil fixe, en attendant l'accueil construit à partir
des données de l'atelier.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

ACCUEIL = "Bonjour, je suis Nova. Posez-moi vos questions sur l'atelier."


def generer_accueil(db: Session) -> list[str]:
    """Phrases d'accueil affichées à l'ouverture d'une conversation."""
    return [ACCUEIL]
