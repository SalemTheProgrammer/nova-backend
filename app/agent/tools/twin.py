"""Outil agent pour PILOTER le jumeau numérique 3D de la ligne de conditionnement.

Le jumeau vit côté navigateur (moteur de simulation TypeScript) : cet outil ne
touche donc à aucune base de données. Il renvoie un artifact `twin_command` que
l'interface applique directement au jumeau (démarrer/arrêter la ligne, régler la
vitesse, la cadence, les taux de défauts, déclencher une panne démo, changer de
vue caméra, publier vers le MES, vider la ligne…). C'est l'équivalent, piloté par
Nova, de l'ancien pupitre de réglages manuels : l'opérateur parle, Nova règle.

Action « douce » (pilotage d'une simulation, rien d'irréversible côté atelier réel)
donc PAS de confirmation nécessaire.
"""
from __future__ import annotations

import unicodedata

from langchain_core.tools import tool

# Actions reconnues par le jumeau côté frontend (voir twinBus.ts).
ACTIONS = {
    "demarrer",
    "pause",
    "arreter",
    "panne",
    "resoudre",
    "vitesse",
    "cadence",
    "defauts",
    "publier",
    "annotations",
    "vue",
    "reset",
}

# Postes de la ligne (ids frontend) + « tout » pour toute la ligne.
_POSTES = {"blistereuse", "trieuse", "vignetteuse", "tout"}
# Préréglages caméra reconnus par la scène 3D.
_VUES = {"ensemble", "blistereuse", "trieuse", "vignetteuse", "rejets"}

_NOMS = {
    "blistereuse": "la blistéreuse",
    "trieuse": "la trieuse pondérale",
    "vignetteuse": "la vignetteuse",
    "tout": "toute la ligne",
}


def _sans_accents(txt: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn"
    ).lower().strip()


def _normaliser_poste(poste: str | None) -> str | None:
    """Ramène un libellé libre vers un id de poste (ou 'tout'). None si absent."""
    if poste is None:
        return None
    p = _sans_accents(poste)
    if any(k in p for k in ("tout", "ligne", "all", "entier", "complet")):
        return "tout"
    if "blist" in p:
        return "blistereuse"
    if any(k in p for k in ("trieuse", "ponder", "pesee", "pesage", "checkweig")):
        return "trieuse"
    if any(k in p for k in ("vignet", "etiquet", "label")):
        return "vignetteuse"
    return None


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
    """Pilote le jumeau numérique 3D de la ligne de conditionnement (blistéreuse,
    trieuse pondérale, vignetteuse). C'est TOI qui règles le jumeau à la place de
    l'opérateur : il n'y a plus de boutons/curseurs manuels, tout passe par toi.

    `action` doit valoir exactement l'une de :
    - "demarrer" / "pause" / "arreter" : commande la marche de la ligne. `poste`
      cible un poste précis ('blistereuse', 'trieuse', 'vignetteuse') ou 'tout'
      (par défaut : toute la ligne).
    - "panne" : déclenche une panne de démonstration sur `poste` (par défaut la
      trieuse). "resoudre" : lève la panne d'un poste.
    - "vitesse" : règle la vitesse de simulation, `valeur` entre 0.5 et 3 (×).
      C'est LE levier quand l'opérateur dit « accélère » / « plus vite » / « c'est
      lent » sans parler de cadence : bandes, produits et machines accélèrent tous.
    - "cadence" : règle le rythme de sortie de la blistéreuse. `valeur` de 1.5 à 8
      = temps de cycle en SECONDES par blister — attention, cadence plus rapide =
      valeur plus PETITE (1.5 s = très rapide, 8 s = lent). Une `valeur` > 8 est
      lue comme un débit en blisters/minute et convertie (ex. 30 → 2 s/blister).
      « Accélère la cadence » sans chiffre → envoie 2.
    - "defauts" : règle le taux de défauts d'un `poste` (ou 'tout'), `valeur` en
      pourcentage (0 à 90) ou en fraction (0 à 0.9).
    - "publier" : active (`valeur`=1) ou coupe (`valeur`=0) l'envoi des pièces/
      rejets du jumeau vers le MES.
    - "annotations" : affiche (1) / masque (0) les étiquettes des machines.
    - "vue" : déplace la caméra ; `poste` ∈ 'ensemble', 'blistereuse', 'trieuse',
      'vignetteuse', 'rejets'.
    - "reset" : vide la ligne et remet les compteurs à zéro.

    À utiliser dès que l'opérateur demande d'agir sur le jumeau / la ligne
    simulée (« démarre la ligne », « accélère », « mets 30 % de défauts sur la
    blistéreuse », « déclenche une panne », « montre la vignetteuse », « remets à
    zéro »). Pilotage d'une simulation : aucune confirmation nécessaire.
    """
    act = _sans_accents(action).replace("é", "e")
    # Quelques synonymes courants → action canonique.
    _ALIAS = {
        "start": "demarrer",
        "lancer": "demarrer",
        "demarre": "demarrer",
        "stop": "arreter",
        "arrete": "arreter",
        "arret": "arreter",
        "vider": "reset",
        "reinitialiser": "reset",
        "raz": "reset",
        "speed": "vitesse",
        "cycle": "cadence",
        "defaut": "defauts",
        "camera": "vue",
    }
    act = _ALIAS.get(act, act)
    if act not in ACTIONS:
        return (
            f"Action inconnue : {action!r}. Actions valides : {', '.join(sorted(ACTIONS))}.",
            None,
        )

    artifact: dict = {"kind": "twin_command", "action": act}
    msg: str

    if act in {"demarrer", "pause", "arreter"}:
        cible = _normaliser_poste(poste) or "tout"
        artifact["cible"] = cible
        verbe = {"demarrer": "Démarrage", "pause": "Pause", "arreter": "Arrêt"}[act]
        msg = f"{verbe} de {_NOMS[cible]} sur le jumeau."

    elif act == "panne":
        cible = _normaliser_poste(poste)
        cible = "trieuse" if cible in (None, "tout") else cible
        artifact["cible"] = cible
        msg = f"Panne de démonstration déclenchée sur {_NOMS[cible]}."

    elif act == "resoudre":
        cible = _normaliser_poste(poste)
        cible = "trieuse" if cible in (None, "tout") else cible
        artifact["cible"] = cible
        msg = f"Panne levée sur {_NOMS[cible]} : la production reprend."

    elif act == "vitesse":
        if valeur is None:
            return "Précise la vitesse voulue (entre 0.5 et 3).", None
        v = max(0.5, min(3.0, float(valeur)))
        artifact["valeur"] = v
        msg = f"Vitesse de simulation réglée à ×{v:g}."

    elif act == "cadence":
        if valeur is None:
            return (
                "Précise la cadence voulue : en secondes par blister (1.5 à 8, "
                "plus petit = plus rapide) ou en blisters/minute (> 8).",
                None,
            )
        v = float(valeur)
        # Le reste du MES exprime la cadence en unités/minute (plus grand = plus
        # rapide) alors qu'ici c'est un temps de cycle. Les plages ne se recouvrent
        # pas : au-delà de 8, la valeur est un débit qu'on convertit en temps de cycle.
        if v > 8.0:
            v = 60.0 / v
        v = max(1.5, min(8.0, v))
        artifact["valeur"] = v
        msg = f"Cadence blistéreuse réglée à {v:g} s/blister (≈ {round(60 / v)} blisters/min)."

    elif act == "defauts":
        if valeur is None:
            return "Précise le taux de défauts voulu (en % ou en fraction).", None
        v = float(valeur)
        frac = v / 100 if v > 1 else v
        frac = max(0.0, min(0.9, frac))
        cible = _normaliser_poste(poste) or "tout"
        artifact["cible"] = cible
        artifact["valeur"] = frac
        msg = f"Taux de défauts de {_NOMS[cible]} réglé à {round(frac * 100)} %."

    elif act in {"publier", "annotations"}:
        etat = True if valeur is None else bool(float(valeur))
        artifact["valeur"] = etat
        if act == "publier":
            msg = (
                "Publication des pièces du jumeau vers le MES activée."
                if etat
                else "Publication vers le MES coupée : le jumeau tourne en local."
            )
        else:
            msg = "Annotations des machines affichées." if etat else "Annotations masquées."

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

    else:  # reset
        msg = "Ligne vidée et compteurs remis à zéro sur le jumeau."

    return msg, artifact


TWIN_TOOLS = [piloter_jumeau_numerique]
