"""Garde-fou structurel pour les outils IRRÉVERSIBLES / SORTANTS.

Jusqu'ici, le paramètre `confirmation` d'un outil était fixé par le LLM
lui-même : le prompt système lui demande de décrire l'action et d'attendre un
« oui » avant de rappeler l'outil avec `confirmation=true`, mais RIEN au niveau
code n'empêchait un modèle (surtout un modèle rapide/léger) d'enchaîner
vérification + création dans le MÊME tour de raisonnement si la demande
initiale de l'opérateur sonnait comme une autorisation implicite (ex. « lance
300 boîtes de PARA500 » peut se lire comme un ordre direct plutôt qu'une simple
description du besoin).

Ce module ajoute une règle qui ne dépend PAS du prompt : une action ne peut
s'exécuter que si sa signature (outil + arguments métier) a déjà été PROPOSÉE
lors d'un APPEL API ANTÉRIEUR — jamais lors du même appel que sa première
apparition — quelle que soit la valeur de `confirmation` envoyée par le modèle.

Point critique : le graphe LangGraph boucle agent ⇄ tools PLUSIEURS FOIS au
sein d'un même appel HTTP (`run_agent`/`stream_agent`, un appel = un message
opérateur). Un modèle véloce pourrait donc, sans aucune nouvelle intervention
humaine, appeler l'outil une première fois avec confirmation=false, lire dans
la boucle le message « confirmation requise », puis se rappeler LUI-MÊME avec
confirmation=true — tout cela dans le MÊME appel API. Se fier uniquement à
« la signature a déjà été vue » ne suffit donc pas : il faut aussi vérifier que
la confirmation arrive dans un appel API DIFFÉRENT (donc après un vrai message
opérateur supplémentaire).

`runner.run_agent` / `runner.stream_agent` génèrent un `invocation_id` (UUID)
neuf à chaque appel et l'injectent dans `config["configurable"]` — c'est ce qui
matérialise « un appel différent ». Une proposition n'est confirmable que
depuis un `invocation_id` distinct de celui qui l'a créée.

Limite connue : le registre est en mémoire process (comme `MemorySaver` pour la
mémoire de conversation, voir `agent/graph.py`) — suffisant pour un déploiement
mono-processus / démo, à remplacer par un stockage partagé (Redis, table SQL)
si le backend tourne un jour en plusieurs workers.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from langchain_core.runnables import RunnableConfig


@dataclass
class _Proposition:
    invocation_id: str | None
    proposed_at: float


# thread_id -> { signature: _Proposition }
_PROPOSITIONS: dict[str, dict[str, _Proposition]] = {}

# Une proposition non confirmée expire : un « oui » tapé bien après (contexte
# oublié, situation ayant changé) ne doit pas ré-exécuter une vieille action.
EXPIRATION_S = 15 * 60


def _signature(tool_name: str, args_metier: dict) -> str:
    brut = tool_name + json.dumps(args_metier, sort_keys=True, default=str)
    return hashlib.sha256(brut.encode()).hexdigest()[:16]


def _purger(propositions: dict[str, _Proposition]) -> None:
    seuil = time.time() - EXPIRATION_S
    for sig in [s for s, p in propositions.items() if p.proposed_at < seuil]:
        propositions.pop(sig, None)


def _configurable(config: RunnableConfig | None) -> dict:
    return (config or {}).get("configurable") or {}


def evaluer(
    config: RunnableConfig | None,
    tool_name: str,
    args_metier: dict,
    confirmation: bool,
) -> bool:
    """Renvoie True si l'exécution réelle est autorisée MAINTENANT.

    - `confirmation=False` : enregistre la proposition (confirmable depuis un
      appel API ultérieur) et renvoie False.
    - `confirmation=True` mais signature jamais proposée pour ce thread, OU
      proposée mais depuis le MÊME appel API (même `invocation_id`) : REFUSÉ.
      C'est la protection contre un modèle qui boucle tout seul sur le même
      message opérateur pour s'auto-confirmer.
    - `confirmation=True`, signature proposée lors d'un appel API antérieur
      (invocation_id différent) : autorisé, la proposition est consommée.

    Sans `thread_id`/`invocation_id` dans le config (ne devrait pas arriver via
    l'API, qui les fournit toujours), on retombe sur la confiance au booléen
    pour ne pas bloquer un usage direct/test.
    """
    cfg = _configurable(config)
    thread_id = cfg.get("thread_id")
    invocation_id = cfg.get("invocation_id")
    if thread_id is None or invocation_id is None:
        return confirmation

    propositions = _PROPOSITIONS.setdefault(thread_id, {})
    _purger(propositions)
    sig = _signature(tool_name, args_metier)
    existante = propositions.get(sig)

    if not confirmation:
        # Ne pas écraser un invocation_id déjà enregistré par un appel API
        # antérieur (sinon un modèle pourrait « re-proposer » dans le même
        # appel pour rafraîchir l'ancrage et se confirmer ensuite lui-même).
        if existante is None:
            propositions[sig] = _Proposition(invocation_id, time.time())
        return False

    if existante is None or existante.invocation_id == invocation_id:
        if existante is None:
            propositions[sig] = _Proposition(invocation_id, time.time())
        return False

    propositions.pop(sig, None)
    return True
