"""Outil agent : graphiques dynamiques à la demande.

L'opérateur demande une visualisation en langage naturel (« montre-moi la courbe
du TRS de la ligne 1 », « un Pareto des arrêts ») ; l'agent choisit le dataset
et les paramètres dans le catalogue de `chart_service`, et le frontend rend
l'artifact `{kind: "chart"}` en graphique interactif — dans le chat et sous la
bulle du mode vocal.
"""
from __future__ import annotations

from langchain_core.tools import tool

from sqlalchemy import select

from app.db.session import session_scope
from app.models import LigneProduction, Machine, OrdreFabrication
from app.services import chart_service, trs_service


def _resumer_spec(spec: dict, periode_heures: int, dataset: str) -> str:
    """Résumé chiffré du graphique, pour que l'agent le commente JUSTE.

    L'agent ne « voit » pas l'image qu'il envoie : sans ces chiffres il commente
    au hasard (ou écrit un placeholder du genre « tendance à confirmer »). On lui
    rend donc l'essentiel de chaque série : début → fin, moyenne, extrêmes, et le
    plus gros poste pour les graphiques triés (Pareto, rebuts, comparaisons).
    """
    unite = spec.get("unit") or ""
    # Datasets catégoriels : l'intéressant est le classement, pas la chronologie.
    categoriel = dataset in (
        "pareto_arrets",
        "rebuts_par_cause",
        "trs_machines",
        "stock_matieres",
    )
    lignes: list[str] = []
    for serie in spec.get("series", []):
        points = [p for p in serie.get("data", []) if p.get("y") is not None]
        if not points:
            continue
        ys = [float(p["y"]) for p in points]
        nom = serie.get("name") or "série"
        haut = max(points, key=lambda p: float(p["y"]))
        bas = min(points, key=lambda p: float(p["y"]))
        if categoriel:
            classement = sorted(points, key=lambda p: float(p["y"]), reverse=True)[:3]
            details = [
                "en tête : "
                + ", ".join(f"{p.get('x')} {float(p['y']):.1f}{unite}" for p in classement),
                f"plus bas : {bas.get('x')} {float(bas['y']):.1f}{unite}",
                f"total {sum(ys):.0f}{unite}" if unite != "%" else "",
            ]
        else:
            details = [
                f"début {ys[0]:.1f}{unite} → fin {ys[-1]:.1f}{unite}",
                f"moyenne {sum(ys) / len(ys):.1f}{unite}",
                f"plus haut {float(haut['y']):.1f}{unite} à {haut.get('x')}",
                f"plus bas {float(bas['y']):.1f}{unite} à {bas.get('x')}",
                f"total {sum(ys):.0f}{unite}" if unite != "%" else "",
            ]
        lignes.append(f"{nom} : " + ", ".join(d for d in details if d))
    if not lignes:
        return ""
    return (
        f"Fenêtre {periode_heures} h. " + " | ".join(lignes) + " "
        "Commente CES chiffres en une phrase (tendance, valeur clé, poste dominant) "
        "et nomme les éléments cités : n'invente aucun chiffre, n'écris jamais de "
        "placeholder, et ne recopie pas ce résumé technique tel quel."
    )


def _trouver_of(db, numero_ou_id: str) -> OrdreFabrication | None:
    of: OrdreFabrication | None = None
    if numero_ou_id.isdigit():
        of = db.get(OrdreFabrication, int(numero_ou_id))
    if of is None:
        of = db.execute(
            select(OrdreFabrication).where(OrdreFabrication.numero == numero_ou_id)
        ).scalars().first()
    return of


@tool(response_format="content_and_artifact")
def generer_graphique(
    dataset: str,
    scope: str = "usine",
    id: int | None = None,
    of_numero: str | None = None,
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

    `scope` : "usine" (défaut), "ligne", "machine" ou "of" — avec `id` pour
    ligne/machine. Pour "of", utilisez `of_numero` (ex. 'OF-2026-00039') plutôt
    que `id` : c'est le seul moyen fiable de cibler un OF précis, l'agent n'a
    normalement pas son id numérique interne sous la main.
    `periode_heures` : fenêtre d'analyse (1 à 48, défaut 8).
    `type_graphique` : "line", "bar", "area" ou "pie" pour surcharger le type par
    défaut du dataset (ex. l'opérateur demande explicitement un camembert).

    Après l'appel, commente le graphique en une phrase (tendance, valeur clé) —
    ne décris pas les points un par un, l'opérateur a le visuel sous les yeux.
    """
    with session_scope() as db:
        if scope == "of":
            reference = of_numero or (str(id) if id is not None else None)
            if reference is None:
                return "scope 'of' nécessite of_numero (ex. 'OF-2026-00039').", None
            of = _trouver_of(db, reference)
            if of is None:
                return f"OF introuvable : {reference}.", None
            id = of.id
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
        # Texte rendu au modèle : des CHIFFRES, pas un libellé à recopier — il ne
        # « voit » pas l'image qui part à l'opérateur (chat ou image WhatsApp).
        return f"{spec['title']}. " + _resumer_spec(spec, periode_heures, dataset), spec


@tool(response_format="content_and_artifact")
def generer_jauge(
    indicateur: str = "trs",
    scope: str = "usine",
    id: int | None = None,
    of_numero: str | None = None,
    periode_heures: int = 8,
) -> tuple[str, dict | None]:
    """Affiche une JAUGE (cadran semi-circulaire, comme le dashboard) pour un
    indicateur en pourcentage. À utiliser quand l'opérateur veut UNE valeur
    visuelle (« la jauge du TRS », « où en est le TRS de M-01 », « score OEE »)
    plutôt qu'une évolution dans le temps (là, préférer `generer_graphique`).

    `indicateur` : "trs" (défaut), "trg", "tre", "qualite" (taux qualité TQ),
    "performance" (TP) ou "disponibilite" (DO).
    `scope` : "usine" (défaut), "ligne", "machine" ou "of" — avec `id` pour
    ligne/machine. Pour "of", utilisez `of_numero` (ex. 'OF-2026-00039') plutôt
    que `id`.
    `periode_heures` : fenêtre d'analyse (1 à 48, défaut 8).

    Sur WhatsApp la jauge part en image dans la conversation ; commente-la en
    une phrase (valeur, écart à l'objectif de 74 %).
    """
    indicateurs = ("trs", "trg", "tre", "qualite", "performance", "disponibilite")
    if indicateur not in indicateurs:
        return f"Indicateur invalide : {indicateur!r}. Choix : {', '.join(indicateurs)}.", None
    periode_heures = max(1, min(48, periode_heures))
    with session_scope() as db:
        if scope == "of":
            reference = of_numero or (str(id) if id is not None else None)
            if reference is None:
                return "scope 'of' nécessite of_numero (ex. 'OF-2026-00039').", None
            of = _trouver_of(db, reference)
            if of is None:
                return f"OF introuvable : {reference}.", None
            resultat = trs_service.calculer_trs_ordre(db, of)
            libelle = f"OF {of.numero}"
        elif scope == "machine" and id is not None:
            machine = db.get(Machine, id)
            if machine is None:
                return f"Machine introuvable (id={id}).", None
            if not machine.temps_cycle_cible_s:
                return f"{machine.code} n'a pas de temps de cycle cible : TRS incalculable.", None
            resultat = trs_service.calculer_trs_machine(db, machine)
            libelle = f"{machine.code} — {machine.nom}"
        elif scope == "ligne" and id is not None:
            ligne = db.get(LigneProduction, id)
            if ligne is None:
                return f"Ligne introuvable (id={id}).", None
            machines_ligne = (
                db.query(Machine)
                .filter(Machine.ligne_production_id == ligne.id, Machine.actif.is_(True))
                .all()
            )
            resultat = trs_service.calculer_trs_ligne(db, machines_ligne)
            if resultat is None:
                return f"Aucune machine active avec temps de cycle sur {ligne.designation}.", None
            libelle = ligne.designation
        else:
            machines = [
                m for m in db.query(Machine).filter(Machine.actif.is_(True)).all()
                if m.temps_cycle_cible_s
            ]
            if not machines:
                return "Aucune machine active avec temps de cycle : jauge impossible.", None
            resultats = [trs_service.calculer_trs_machine(db, m) for m in machines]
            valeurs = {
                "trs": sum(float(r.trs) for r in resultats) / len(resultats),
                "trg": sum(float(r.trg) for r in resultats) / len(resultats),
                "tre": sum(float(r.tre) for r in resultats) / len(resultats),
                "qualite": sum(float(r.tq) for r in resultats) / len(resultats),
                "performance": sum(float(r.tp) for r in resultats) / len(resultats),
                "disponibilite": sum(float(r.do) for r in resultats) / len(resultats),
            }
            pct = valeurs[indicateur] * 100
            titre = f"{indicateur.upper()} — usine"
            return (
                f"📟 Jauge affichée : {titre} à {pct:.0f} %.",
                {
                    "kind": "gauge", "title": titre, "valeur_pct": round(pct, 1),
                    "objectif_pct": 74.0 if indicateur in ("trs", "trg", "tre") else None,
                    "sous_titre": f"Moyenne de {len(machines)} machine(s) — {periode_heures} h",
                },
            )

        valeurs = {
            "trs": float(resultat.trs), "trg": float(resultat.trg), "tre": float(resultat.tre),
            "qualite": float(resultat.tq), "performance": float(resultat.tp),
            "disponibilite": float(resultat.do),
        }
        pct = valeurs[indicateur] * 100
        titre = f"{indicateur.upper()} — {libelle}"
        return (
            f"📟 Jauge affichée : {titre} à {pct:.0f} %.",
            {
                "kind": "gauge", "title": titre, "valeur_pct": round(pct, 1),
                "objectif_pct": 74.0 if indicateur in ("trs", "trg", "tre") else None,
                "sous_titre": f"Fenêtre {periode_heures} h",
            },
        )


CHART_TOOLS = [generer_graphique, generer_jauge]
