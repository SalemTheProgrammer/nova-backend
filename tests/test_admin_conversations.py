"""Page admin « Conversations » : registre thread → utilisateur, accès en lecture
seule pour l'admin, historique d'un thread réservé à son auteur."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.utilisateur import Utilisateur
from app.services import conversation_service

HISTORIQUE = [
    {"id": "h-0", "role": "user", "segments": [
        {"type": "text", "content": "[WhatsApp — Chef +21622222222] Situation de l'atelier ?"}
    ]},
    {"id": "a-0", "role": "assistant", "segments": [{"type": "text", "content": "3 845 pièces."}]},
]


@pytest.fixture
def nova_db(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope():
        s = Session()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    def get_db_test():
        with scope() as s:
            yield s

    monkeypatch.setattr(conversation_service, "session_scope", scope)
    # Checkpointer factice : un ancien thread WhatsApp, antérieur au registre.
    historique = tmp_path / "nova_chat_history.db"
    con = sqlite3.connect(historique)
    con.execute("CREATE TABLE checkpoints (thread_id TEXT)")
    con.executemany(
        "INSERT INTO checkpoints VALUES (?)",
        [("wa:+21622222222",), ("wa:+21622222222",), ("web-ancien",)],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(conversation_service, "chat_history_db_path", lambda: str(historique))

    with scope() as db:
        db.add_all([
            Utilisateur(id=1, telephone="+21600000000", nom_complet="Admin Test", is_admin=True),
            Utilisateur(id=2, telephone="+21600000001", nom_complet="Opérateur Test"),
            Utilisateur(id=3, telephone="+21622222222", nom_complet="Chef"),
        ])
    yield scope, get_db_test
    engine.dispose()


def test_enregistrer_cree_et_ne_vole_pas(nova_db):
    scope, _ = nova_db
    conversation_service.enregistrer("t-1", "web", utilisateur_id=2, message="Bonjour Nova")
    conversation_service.enregistrer("t-1", "web", utilisateur_id=3, message="autre")
    with scope() as db:
        conv = db.get(Conversation, "t-1")
        assert conv.utilisateur_id == 2
        assert conv.apercu == "Bonjour Nova"


def test_apercu_sans_prefixe_whatsapp(nova_db):
    scope, _ = nova_db
    conversation_service.enregistrer(
        "wa:+21622222222", "whatsapp", utilisateur_id=3, numero="+21622222222",
        message="[WhatsApp — Chef +21622222222] Situation de l'atelier ?",
    )
    with scope() as db:
        assert db.get(Conversation, "wa:+21622222222").apercu == "Situation de l'atelier ?"


def test_peut_lire(nova_db):
    scope, _ = nova_db
    conversation_service.enregistrer("t-chef", "web", utilisateur_id=3)
    with scope() as db:
        admin, operateur, chef = (db.get(Utilisateur, i) for i in (1, 2, 3))
    assert conversation_service.peut_lire(chef, "t-chef")
    assert conversation_service.peut_lire(admin, "t-chef")
    assert not conversation_service.peut_lire(operateur, "t-chef")
    assert conversation_service.peut_lire(operateur, "thread-inconnu")


def test_historique_d_un_collegue_refuse(nova_db, client_sans_outils: TestClient):
    conversation_service.enregistrer("t-chef", "web", utilisateur_id=3)
    with patch("app.api.routes.chat.get_thread_history", new=AsyncMock(return_value=[])):
        assert client_sans_outils.get("/api/v1/chat/t-chef/history").status_code == 404
        assert client_sans_outils.delete("/api/v1/chat/t-chef").status_code == 404
        assert client_sans_outils.get("/api/v1/chat/t-nouveau/history").status_code == 200


def test_chat_rattache_le_thread_a_son_auteur(nova_db, client_sans_outils: TestClient):
    scope, _ = nova_db
    with patch("app.api.routes.chat.run_agent", new=AsyncMock(return_value="ok")):
        resp = client_sans_outils.post(
            "/api/v1/chat", json={"message": "Quels OF sont en retard ?", "thread_id": "t-op"}
        )
    assert resp.status_code == 200
    with scope() as db:
        conv = db.get(Conversation, "t-op")
        assert (conv.utilisateur_id, conv.canal, conv.apercu) == (2, "web", "Quels OF sont en retard ?")


def test_admin_liste_et_detail(nova_db, auth_client: TestClient):
    _, get_db_test = nova_db
    auth_client.app.dependency_overrides[get_db] = get_db_test
    conversation_service.enregistrer("t-op", "web", utilisateur_id=2, message="Bonjour")

    liste = auth_client.get("/api/v1/admin/conversations").json()
    par_thread = {c["thread_id"]: c for c in liste}
    # L'ancien thread WhatsApp du checkpointer est rattaché au compte du numéro.
    assert par_thread["wa:+21622222222"]["utilisateur_nom"] == "Chef"
    assert par_thread["wa:+21622222222"]["canal"] == "whatsapp"
    assert par_thread["t-op"]["utilisateur_nom"] == "Opérateur Test"
    assert "web-ancien" not in par_thread

    filtre = auth_client.get("/api/v1/admin/conversations", params={"utilisateur_id": 3}).json()
    assert [c["thread_id"] for c in filtre] == ["wa:+21622222222"]

    with patch(
        "app.api.routes.admin_conversations.get_thread_history",
        new=AsyncMock(return_value=[dict(t, segments=[dict(s) for s in t["segments"]]) for t in HISTORIQUE]),
    ):
        detail = auth_client.get("/api/v1/admin/conversations/wa:+21622222222").json()
    assert detail["conversation"]["telephone"] == "+21622222222"
    assert detail["turns"][0]["segments"][0]["content"] == "Situation de l'atelier ?"
    assert auth_client.get("/api/v1/admin/conversations/inconnu").status_code == 404


def test_admin_conversations_reserve_a_l_admin(nova_db, client_sans_outils: TestClient):
    assert client_sans_outils.get("/api/v1/admin/conversations").status_code == 403
