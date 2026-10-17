"""Chat endpoint tests with the agent runner mocked out."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_chat_requires_authentification(client: TestClient) -> None:
    resp = client.post("/api/v1/chat", json={"message": "hello"})
    assert resp.status_code == 401


def test_chat_returns_agent_response(auth_client: TestClient) -> None:
    with patch("app.api.routes.chat.run_agent", new=AsyncMock(return_value="hi there")):
        resp = auth_client.post(
            "/api/v1/chat",
            json={"message": "hello", "thread_id": "t-1"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["response"] == "hi there"
    assert body["thread_id"] == "t-1"


def test_chat_validates_empty_message(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/v1/chat", json={"message": ""})
    assert resp.status_code == 422


def _accueil(client: TestClient) -> list[str]:
    with patch("app.api.routes.chat.enregistrer_accueil", new=AsyncMock(return_value=True)), \
            patch(
                "app.api.routes.chat._accueil_depuis_atelier",
                return_value=["Bonjour, je suis Nova. Données au 28/08/2026.", "Chiffres"],
            ):
        resp = client.post("/api/v1/chat/accueil", json={"thread_id": "t-acc"})
    assert resp.status_code == 200
    return resp.json()["messages"]


def test_accueil_neutre_sans_outil_atelier(client_sans_outils: TestClient) -> None:
    assert _accueil(client_sans_outils) == ["Bonjour, je suis Nova.", "Comment puis-je vous aider ?"]


def test_accueil_atelier_pour_admin(auth_client: TestClient) -> None:
    assert _accueil(auth_client)[1] == "Chiffres"


def test_accueil_atelier_pour_outil_atelier() -> None:
    from app.core.security import get_current_user
    from app.main import create_app
    from app.models.utilisateur import Utilisateur

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: Utilisateur(
        id=4, telephone="+21600000004", nom_complet="Chef", is_admin=False, actif=True,
        outils_autorises=["situation_atelier"],
    )
    assert _accueil(TestClient(app))[1] == "Chiffres"
