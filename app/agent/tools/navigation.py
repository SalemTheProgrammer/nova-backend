"""Outil de navigation : redirige l'interface de l'opérateur vers la page concernée.

Action « douce » (ne modifie rien) : renvoie un artifact que le frontend utilise
pour changer de vue automatiquement, pas de confirmation nécessaire. Les clés de
page DOIVENT correspondre exactement aux vues du frontend (`NavView` dans
`Sidebar.tsx`) sous peine de ne rien faire côté UI.
"""
from __future__ import annotations

from langchain_core.tools import tool

PAGES: dict[str, str] = {
    "atelier": "Tableau de bord",
    "chaines": "Chaînes",
    "of": "Ordres de fabrication",
    "operateurs": "Opérateurs",
    "commandes": "Commandes & livraisons",
    "rapports": "Rapports",
    "documents": "Documents",
}


@tool(response_format="content_and_artifact")
def aller_a_la_page(page: str, raison: str | None = None) -> tuple[str, dict | None]:
    """Redirige l'interface vers la page correspondant à la question de l'opérateur.

    `page` doit être exactement l'une de : atelier, chaines, of, operateurs, commandes, rapports,
    documents.

    À appeler dès que la réponse concerne un domaine ayant une page dédiée, pour
    que l'opérateur voie l'information en contexte pendant que tu réponds. Ne
    modifie rien : pas de confirmation nécessaire.
    """
    label = PAGES.get(page)
    if label is None:
        return (
            f"Page inconnue : {page!r}. Pages valides : {', '.join(PAGES)}.",
            None,
        )
    message = f"📍 Page ouverte : {label}" + (f" — {raison}" if raison else "")
    artifact = {"kind": "navigation", "page": page, "label": label, "raison": raison}
    return message, artifact


NAVIGATION_TOOLS = [aller_a_la_page]

# Filet de sécurité déterministe : le LLM n'appelle pas toujours `aller_a_la_page`
# de lui-même (modèles légers, prompts ambigus). Cette table associe chaque outil
# de lecture/action à la page qu'il concerne ; `runner.stream_agent` l'utilise
# pour émettre automatiquement une navigation après l'appel réel de l'outil,
# sans dépendre de la décision du LLM. `aller_a_la_page` reste disponible pour
# les cas explicites (« ouvre-moi la page stock ») qu'aucun autre outil ne couvre.
TOOL_PAGE_MAP: dict[str, str] = {
    "situation_atelier": "atelier",
    "production_chaines": "atelier",
    "detail_chaine": "chaines",
    "rechercher_of": "of",
    "avancement_of": "of",
    "rendement_operateurs": "operateurs",
    "fiche_operateur": "operateurs",
    "commandes_a_risque": "commandes",
    "consulter_commande": "commandes",
    "rapport_pdf": "rapports",
}
