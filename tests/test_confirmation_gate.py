"""Tests du garde-fou de confirmation (`agent/tools/confirmation_gate`).

C'est le composant de sécurité central du système : aucune action irréversible
ne doit pouvoir s'exécuter sans un aller-retour réel avec l'opérateur (deux
appels API distincts). Ces tests vérifient la règle indépendamment du LLM.
"""
from __future__ import annotations

import time

import pytest

from app.agent.tools import confirmation_gate


def _config(thread_id: str = "t-1", invocation_id: str = "inv-1") -> dict:
    return {"configurable": {"thread_id": thread_id, "invocation_id": invocation_id}}


ARGS = {"machine_id": 3}


@pytest.fixture(autouse=True)
def _registre_propre(monkeypatch: pytest.MonkeyPatch):
    confirmation_gate._PROPOSITIONS.clear()
    # Le chemin succès du garde-fou écrit le journal d'audit : pas de base ici.
    from app.services import audit_service

    monkeypatch.setattr(audit_service, "enregistrer_action", lambda **kwargs: None)
    yield
    confirmation_gate._PROPOSITIONS.clear()


def test_proposition_sans_confirmation_est_refusee_et_enregistree() -> None:
    autorise = confirmation_gate.evaluer(_config(), "arreter_machine", ARGS, confirmation=False)
    assert autorise is False
    assert confirmation_gate._PROPOSITIONS["t-1"]  # la proposition est ancrée


def test_auto_confirmation_dans_le_meme_appel_est_refusee() -> None:
    """Le modèle propose puis se re-confirme lui-même dans la même boucle
    agent⇄tools (même invocation_id) : REFUSÉ."""
    cfg = _config(invocation_id="inv-1")
    confirmation_gate.evaluer(cfg, "arreter_machine", ARGS, confirmation=False)
    autorise = confirmation_gate.evaluer(cfg, "arreter_machine", ARGS, confirmation=True)
    assert autorise is False


def test_confirmation_depuis_un_appel_ulterieur_est_autorisee_puis_consommee() -> None:
    confirmation_gate.evaluer(
        _config(invocation_id="inv-1"), "arreter_machine", ARGS, confirmation=False
    )
    # Nouveau message opérateur = nouvel invocation_id : autorisé.
    autorise = confirmation_gate.evaluer(
        _config(invocation_id="inv-2"), "arreter_machine", ARGS, confirmation=True
    )
    assert autorise is True
    # La proposition est consommée : rejouer le même "oui" ne ré-exécute pas.
    rejouee = confirmation_gate.evaluer(
        _config(invocation_id="inv-3"), "arreter_machine", ARGS, confirmation=True
    )
    assert rejouee is False


def test_confirmation_sans_proposition_prealable_est_refusee() -> None:
    autorise = confirmation_gate.evaluer(_config(), "arreter_machine", ARGS, confirmation=True)
    assert autorise is False


def test_signature_differente_nest_pas_confirmable() -> None:
    """La confirmation ne vaut que pour EXACTEMENT l'action proposée : d'autres
    arguments métier = autre signature = refus."""
    confirmation_gate.evaluer(
        _config(invocation_id="inv-1"), "arreter_machine", {"machine_id": 3}, confirmation=False
    )
    autorise = confirmation_gate.evaluer(
        _config(invocation_id="inv-2"), "arreter_machine", {"machine_id": 4}, confirmation=True
    )
    assert autorise is False


def test_proposition_expiree_nest_plus_confirmable() -> None:
    """Un « oui » tapé longtemps après (contexte oublié) ne doit rien exécuter."""
    confirmation_gate.evaluer(
        _config(invocation_id="inv-1"), "arreter_machine", ARGS, confirmation=False
    )
    # Vieillit artificiellement la proposition au-delà de l'expiration.
    for prop in confirmation_gate._PROPOSITIONS["t-1"].values():
        prop.proposed_at = time.time() - confirmation_gate.EXPIRATION_S - 1
    autorise = confirmation_gate.evaluer(
        _config(invocation_id="inv-2"), "arreter_machine", ARGS, confirmation=True
    )
    assert autorise is False


def test_re_proposition_ne_rafraichit_pas_l_ancrage() -> None:
    """Un modèle qui re-propose dans un 2e appel puis se confirme dans ce même
    2e appel doit être refusé : l'ancrage reste celui du 1er appel, et la
    confirmation du 2e appel est... autorisée car invocation différente du 1er.
    En revanche, si la MÊME invocation propose puis confirme sans qu'aucune
    proposition antérieure n'existe, c'est refusé (voir test auto-confirmation).
    Ici on vérifie que la re-proposition n'écrase pas l'invocation_id d'origine.
    """
    confirmation_gate.evaluer(
        _config(invocation_id="inv-1"), "arreter_machine", ARGS, confirmation=False
    )
    # Re-proposition dans l'appel 2 : ne doit PAS écraser l'ancrage inv-1.
    confirmation_gate.evaluer(
        _config(invocation_id="inv-2"), "arreter_machine", ARGS, confirmation=False
    )
    (prop,) = confirmation_gate._PROPOSITIONS["t-1"].values()
    assert prop.invocation_id == "inv-1"


def test_threads_isoles() -> None:
    """Une proposition du thread A n'est pas confirmable depuis le thread B."""
    confirmation_gate.evaluer(
        _config(thread_id="t-A", invocation_id="inv-1"), "arreter_machine", ARGS, False
    )
    autorise = confirmation_gate.evaluer(
        _config(thread_id="t-B", invocation_id="inv-2"), "arreter_machine", ARGS, True
    )
    assert autorise is False


def test_sans_config_retombe_sur_le_booleen() -> None:
    """Usage direct/test hors API (pas de thread_id/invocation_id) : on fait
    confiance au booléen pour ne pas bloquer."""
    assert confirmation_gate.evaluer(None, "arreter_machine", ARGS, confirmation=True) is True
    assert confirmation_gate.evaluer(None, "arreter_machine", ARGS, confirmation=False) is False
