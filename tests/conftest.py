"""Test fixtures and environment setup."""
from __future__ import annotations

import os

import pytest

# Ensure config loads without a real .env during tests.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PINECONE_API_KEY", "test-key")
os.environ.setdefault("API_KEYS", "test-api-key")

from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import get_current_user  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.utilisateur import Utilisateur  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": "test-api-key"}


@pytest.fixture
def auth_client() -> TestClient:
    """Client authentifié : `get_current_user` renvoie un administrateur factice
    (accès à tous les outils), sans toucher à la base ni au flux WhatsApp."""
    app = create_app()

    def _fake_user() -> Utilisateur:
        return Utilisateur(
            id=1,
            telephone="+21655516823",
            nom_complet="Admin Test",
            is_admin=True,
            actif=True,
            outils_autorises=[],
        )

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)
