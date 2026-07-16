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
