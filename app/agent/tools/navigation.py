"""Outil de navigation : redirige l'interface de l'opérateur vers la page concernée.

Action « douce » (ne modifie rien) : renvoie un artifact que le frontend utilise
pour changer de vue automatiquement, pas de confirmation nécessaire. Les clés de
page DOIVENT correspondre exactement aux vues du frontend (`NavView` dans
`Sidebar.tsx`) sous peine de ne rien faire côté UI.
"""
from __future__ import annotations

from langchain_core.tools import tool

PAGES: dict[str, str] = {
    "dashboard": "Dashboard",
    "simulateur": "Simulateur",
    "machines": "Machines",
    "trs": "TRS",
    "arrets": "Arrêts",
    "qualite": "Qualité",
    "maintenance": "Maintenance",
    "stock": "Stock",
    "ordres": "Ordres",
    "articles": "Articles",
    "matieres": "Matières premières",
    "lignes": "Lignes",
    "fournisseurs": "Fournisseurs",
    "documents": "Documents",
}


@tool(response_format="content_and_artifact")
def aller_a_la_page(page: str, raison: str | None = None) -> tuple[str, dict | None]:
    """Redirige l'interface vers la page correspondant à la question de l'opérateur.

    `page` doit être exactement l'une de : dashboard, simulateur, machines, trs,
    arrets, qualite, maintenance, stock, ordres, articles, matieres, lignes,
    fournisseurs, documents.

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
    "etat_stock_matiere": "stock",
    "verifier_disponibilite": "stock",
    "lister_articles": "articles",
    "rechercher_article": "articles",
    "creer_ordre_fabrication": "ordres",
    "consulter_ordre_fabrication": "ordres",
    "lister_lignes_production": "lignes",
    "choisir_meilleure_ligne": "lignes",
    "basculer_of_vers_ligne": "ordres",
    "etat_machine": "machines",
    "demarrer_machine": "machines",
    "arreter_machine": "machines",
    "resume_trs": "trs",
    "arrets_actifs": "arrets",
    "resoudre_arret_machine": "arrets",
    "alertes_actives": "dashboard",
    "acquitter_alerte": "dashboard",
    "generer_rapport_production": "dashboard",
    "risque_panne_machines": "maintenance",
    "lancer_maintenance": "maintenance",
    "rechercher_documents": "documents",
}
