"""Outil agent pour régler l'AFFICHAGE du jumeau numérique 3D.

Le jumeau est un pur miroir temps réel du MES : il ne se simule ni ne se pilote
plus depuis l'agent (l'ancien pupitre de simulation — vitesse, cadence, défauts,
panne démo… — a été retiré). Pour agir sur la ligne, on passe par les commandes
SCADA (`demarrer_machine`, `arreter_machine`, …) : le jumeau reflète alors
automatiquement l'état réel. Cet outil ne renvoie qu'un artifact `twin_command`
d'affichage : changer la ligne visualisée (`ligne`), déplacer la caméra (`vue`)
ou montrer/masquer les annotations.

Action « douce » (affichage uniquement) donc PAS de confirmation nécessaire.
"""
from __future__ import annotations

import unicodedata

from langchain_core.tools import tool

# Actions reconnues par le jumeau côté frontend (voir twinBus.ts).
ACTIONS = {"vue", "annotations", "ligne"}

# Anciennes actions de simulation, retirées : le jumeau n'est plus qu'un miroir
# temps réel. On les reconnaît encore pour répondre proprement au lieu de planter.
_ACTIONS_RETIREES = {
    "demarrer",
    "pause",
    "arreter",
    "panne",
    "resoudre",
    "vitesse",
    "cadence",
    "defauts",
    "publier",
    "reset",
}

# Préréglages caméra reconnus par la scène 3D.
_VUES = {"ensemble", "blistereuse", "trieuse", "vignetteuse", "rejets"}


def _sans_accents(txt: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn"
    ).lower().strip()


def _normaliser_vue(poste: str | None) -> str | None:
    if poste is None:
        return None
    p = _sans_accents(poste)
    if any(k in p for k in ("ensemble", "global", "general", "toute", "vue d")):
        return "ensemble"
    if "blist" in p:
        return "blistereuse"
    if any(k in p for k in ("trieuse", "ponder", "pesee", "pesage", "checkweig")):
        return "trieuse"
    if any(k in p for k in ("vignet", "etiquet", "label")):
        return "vignetteuse"
    if any(k in p for k in ("rejet", "rebut", "bac")):
        return "rejets"
    return None


@tool(response_format="content_and_artifact")
def piloter_jumeau_numerique(
    action: str,
    poste: str | None = None,
    valeur: float | None = None,
) -> tuple[str, dict | None]:
    """Règle l'AFFICHAGE du jumeau numérique 3D de la ligne de conditionnement
    (blistéreuse, trieuse pondérale, vignetteuse). Le jumeau est un miroir temps
    réel des vraies machines : il ne se pilote pas — pour démarrer/arrêter une
    machine ou résoudre une panne, utilise les commandes SCADA, le jumeau
    reflétera automatiquement l'état réel.

    `action` doit valoir exactement l'une de :
    - "vue" : déplace la caméra ; `poste` ∈ 'ensemble', 'blistereuse', 'trieuse',
      'vignetteuse', 'rejets'.
    - "annotations" : affiche (`valeur`=1) / masque (`valeur`=0) les étiquettes
      des machines.
    - "ligne" : aligne le jumeau sur une ligne ; `poste` contient son id, son
      code (ex. L2, LIGNE-COMP-03) ou sa désignation.

    À utiliser quand l'opérateur demande à VOIR quelque chose sur le jumeau
    (« montre la vignetteuse », « vue d'ensemble », « zoome sur les bacs de
    rejet », « masque les annotations »). Affichage uniquement : aucune
    confirmation nécessaire.
    """
    act = _sans_accents(action).replace("é", "e")
    # Quelques synonymes courants → action canonique.
    _ALIAS = {"camera": "vue", "voir": "vue", "montrer": "vue", "etiquettes": "annotations"}
    act = _ALIAS.get(act, act)

    if act in _ACTIONS_RETIREES:
        return (
            f"Action {action!r} retirée : le jumeau est un miroir temps réel du MES, "
            "il ne se pilote plus. Pour agir sur la ligne, utilise les commandes "
            "SCADA (demarrer_machine, arreter_machine, resoudre_arret_machine…) : "
            "le jumeau reflétera automatiquement l'état réel.",
            None,
        )
    if act not in ACTIONS:
        return (
            f"Action inconnue : {action!r}. Actions valides : {', '.join(sorted(ACTIONS))}.",
            None,
        )

    artifact: dict = {"kind": "twin_command", "action": act}

    if act == "ligne":
        if not poste or not poste.strip():
            return "Précisez le code, l'id ou le nom de la ligne à visualiser.", None
        artifact["cible"] = poste.strip()
        msg = f"Jumeau aligné sur la ligne {poste.strip()}."
    elif act == "vue":
        vue = _normaliser_vue(poste) or "ensemble"
        artifact["cible"] = vue
        libelle = {
            "ensemble": "vue d'ensemble",
            "blistereuse": "la blistéreuse",
            "trieuse": "la trieuse pondérale",
            "vignetteuse": "la vignetteuse",
            "rejets": "les bacs de rejet",
        }[vue]
        msg = f"Caméra positionnée sur {libelle}."
    else:  # annotations
        etat = True if valeur is None else bool(float(valeur))
        artifact["valeur"] = etat
        msg = "Annotations des machines affichées." if etat else "Annotations masquées."

    return msg, artifact


TWIN_TOOLS = [piloter_jumeau_numerique]
