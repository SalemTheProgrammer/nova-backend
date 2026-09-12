"""Outil agent `decider_proposition` : trancher une décision du superviseur depuis
le chat, derrière le même garde-fou de confirmation que les commandes SCADA."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.agent.tools import confirmation_gate, supervision
from app.models.enums import StatutProposition

PROPOSITION = SimpleNamespace(
    id=7,
    statut=StatutProposition.PROPOSEE,
    titre="OF OF-2026-00002 bloqué par l'arrêt de M-03",
    action_libelle="Basculer l'OF vers la ligne L2",
    resultat=None,
)


class _FauxDb:
    def get(self, _modele: type, proposition_id: int) -> SimpleNamespace | None:
        return PROPOSITION if proposition_id == PROPOSITION.id else None


@contextmanager
def _session():
    yield _FauxDb()


@pytest.fixture(autouse=True)
def _isolation(monkeypatch: pytest.MonkeyPatch):
    confirmation_gate._PROPOSITIONS.clear()
    from app.services import audit_service

    monkeypatch.setattr(audit_service, "enregistrer_action", lambda **kwargs: None)
    monkeypatch.setattr(supervision, "session_scope", _session)
    yield
    confirmation_gate._PROPOSITIONS.clear()


@pytest.fixture
def decisions(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Remplace `supervisor_service.decider` : note les appels, simule l'exécution."""
    appels: list[dict] = []

    def _decider(_db, proposition_id: int, **kwargs) -> SimpleNamespace:
        appels.append({"proposition_id": proposition_id, **kwargs})
        executee = kwargs["approuver"]
        return SimpleNamespace(
            statut=StatutProposition.EXECUTEE if executee else StatutProposition.REJETEE,
            resultat="OF OF-2026-00002 basculé vers L2." if executee else "Rejetée par l'opérateur.",
        )

    monkeypatch.setattr(supervision.supervisor_service, "decider", _decider)
    return appels


def _appel(invocation_id: str, *, confirmation: bool, approuver: bool = True, pid: int = 7):
    """Invoque l'outil comme le fait LangGraph ; renvoie le ToolMessage (texte + artifact)."""
    return supervision.decider_proposition.invoke(
        {
            "name": "decider_proposition",
            "args": {"proposition_id": pid, "approuver": approuver, "confirmation": confirmation},
            "id": f"appel-{invocation_id}",
            "type": "tool_call",
        },
        config={"configurable": {"thread_id": "t-1", "invocation_id": invocation_id}},
    )


def test_premier_appel_demande_l_accord_sans_rien_executer(decisions: list[dict]) -> None:
    message = _appel("inv-1", confirmation=False)
    assert message.artifact["kind"] == "confirmation_attente"
    assert "basculer l'OF vers la ligne L2" in message.artifact["libelle"]
    assert decisions == []


def test_auto_confirmation_dans_le_meme_tour_est_refusee(decisions: list[dict]) -> None:
    _appel("inv-1", confirmation=False)
    message = _appel("inv-1", confirmation=True)
    assert message.artifact["kind"] == "confirmation_attente"
    assert decisions == []


def test_oui_de_l_operateur_execute_la_decision(decisions: list[dict]) -> None:
    _appel("inv-1", confirmation=False)
    message = _appel("inv-2", confirmation=True)

    assert message.content.startswith("✅")
    assert message.artifact["kind"] == "action_executee"
    assert decisions == [
        {"proposition_id": 7, "approuver": True, "canal": "chat", "identite": "thread:t-1"}
    ]


def test_non_de_l_operateur_rejette_la_decision(decisions: list[dict]) -> None:
    _appel("inv-1", confirmation=False, approuver=False)
    message = _appel("inv-2", confirmation=True, approuver=False)

    assert message.content.startswith("Décision ignorée")
    assert decisions[0]["approuver"] is False


def test_decision_introuvable(decisions: list[dict]) -> None:
    message = _appel("inv-1", confirmation=False, pid=999)
    assert "introuvable" in message.content
    assert decisions == []
