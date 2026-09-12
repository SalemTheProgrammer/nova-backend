"""Message d'accueil de Nova : écrit en tête du thread, et gardé dans la fenêtre
envoyée au modèle pour que Nova sache ce qu'elle a dit si l'opérateur y répond."""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agent import graph as graph_module
from app.agent import runner
from app.agent.nodes import agent_node


@pytest.fixture
def graphe_memoire(monkeypatch: pytest.MonkeyPatch) -> None:
    compile = graph_module.build_graph().compile(checkpointer=MemorySaver())
    monkeypatch.setattr(graph_module, "_compiled_graph", compile)


class _ModeleEspion:
    """Remplace le LLM : garde les messages reçus, répond « ok »."""

    def __init__(self) -> None:
        self.recu: list = []

    def bind_tools(self, _outils: list) -> "_ModeleEspion":
        return self

    def invoke(self, messages: list) -> AIMessage:
        self.recu = messages
        return AIMessage(content="ok")


def test_accueil_ecrit_une_seule_fois_en_tete_du_thread(graphe_memoire: None) -> None:
    assert asyncio.run(runner.enregistrer_accueil("t-1", ["Bonjour !", "M-01 est arrêtée."]))
    # Le thread n'est plus vide : un second accueil ne s'ajoute pas.
    assert not asyncio.run(runner.enregistrer_accueil("t-1", ["Encore ?"]))

    tours = asyncio.run(runner.get_thread_history("t-1"))
    assert [t["role"] for t in tours] == ["assistant"]
    assert tours[0]["segments"][0]["content"] == "Bonjour !\n\nM-01 est arrêtée."


def test_accueil_garde_dans_la_fenetre_du_modele(monkeypatch: pytest.MonkeyPatch) -> None:
    espion = _ModeleEspion()
    monkeypatch.setattr(agent_node, "get_chat_model", lambda: espion)
    accueil = AIMessage(
        content="Priorité : l'arrêt de M-03 bloque l'OF-2026-00002.",
        id=f"{agent_node.PREFIXE_ACCUEIL}1",
    )

    agent_node.call_model(
        {"messages": [accueil, HumanMessage(content="ok, règle M-03", id="h-1")]}
    )

    # Sans correction, `start_on="human"` écarterait l'accueil de la fenêtre.
    assert isinstance(espion.recu[0], SystemMessage)
    assert [m.id for m in espion.recu[1:]] == [accueil.id, "h-1"]


def test_un_message_ia_ordinaire_n_est_pas_remonte(monkeypatch: pytest.MonkeyPatch) -> None:
    espion = _ModeleEspion()
    monkeypatch.setattr(agent_node, "get_chat_model", lambda: espion)

    agent_node.call_model(
        {
            "messages": [
                AIMessage(content="réponse orpheline", id="a-0"),
                HumanMessage(content="bonjour", id="h-1"),
            ]
        }
    )

    assert [m.id for m in espion.recu[1:]] == ["h-1"]
