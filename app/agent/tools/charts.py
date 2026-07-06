"""Outil agent : graphiques dynamiques à la demande.

L'opérateur demande une visualisation en langage naturel (« montre-moi la courbe
du TRS de la ligne 1 », « un Pareto des arrêts ») ; l'agent choisit le dataset
et les paramètres dans le catalogue de `chart_service`, et le frontend rend
l'artifact `{kind: "chart"}` en graphique interactif — dans le chat et sous la
bulle du mode vocal.
"""
from __future__ import annotations

from langchain_core.tools import tool

from app.db.session import session_scope
from app.services import chart_service


@tool(response_format="content_and_artifact")
def generer_graphique(
    dataset: str,
    scope: str = "usine",
    id: int | None = None,
    periode_heures: int = 8,
    type_graphique: str | None = None,
) -> tuple[str, dict | None]:
    """Génère un graphique affiché à l'opérateur. À utiliser DÈS QUE l'opérateur
    demande une visualisation (« montre-moi », « courbe », « graphique »,
    « évolution », « répartition », « Pareto », « compare »…) ou quand un chiffre
    seul ne suffit pas à faire comprendre une tendance.

    `dataset` (obligatoire) :
    - trs_horaire : TRS heure par heure (tendance) — courbe.
    - production_horaire : unités bonnes vs rebuts par heure — aires.
    - pareto_arrets : minutes d'arrêt par cause, triées — barres.
    - rebuts_par_cause : répartition des rebuts par cause — camembert.
    - trs_machines : TRS courant de chaque machine (comparaison) — barres.
    - stock_matieres : stock restant par matière première — barres.

    `scope` : "usine" (défaut), "ligne" ou "machine" — avec `id` pour ligne/machine.
    `periode_heures` : fenêtre d'analyse (1 à 48, défaut 8).
    `type_graphique` : "line", "bar", "area" ou "pie" pour surcharger le type par
    défaut du dataset (ex. l'opérateur demande explicitement un camembert).

    Après l'appel, commente le graphique en une phrase (tendance, valeur clé) —
    ne décris pas les points un par un, l'opérateur a le visuel sous les yeux.
    """
    with session_scope() as db:
        try:
            spec = chart_service.construire_graphique(
                db,
                dataset=dataset,
                scope=scope,
                id=id,
                periode_heures=periode_heures,
                chart_type=type_graphique,
            )
        except ValueError as exc:
            return str(exc), None
        nb_points = sum(len(s["data"]) for s in spec["series"])
        if nb_points == 0:
            return (
                f"Aucune donnée pour « {spec['title']} » sur cette période — "
                "le graphique serait vide.",
                None,
            )
        return f"📊 Graphique affiché : {spec['title']}.", spec


CHART_TOOLS = [generer_graphique]
