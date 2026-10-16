"""Sécurité WhatsApp : appairage réservé à l'admin, numéros inconnus refusés."""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.agent.tools import READONLY_TOOL_NAMES
from app.api.routes import whatsapp
from app.core.config import get_settings
from app.db.base import Base
from app.models.utilisateur import Utilisateur

ROUTES_ADMIN = [
    ("get", "/api/v1/whatsapp/qr"),
    ("get", "/api/v1/whatsapp/qr.svg"),
    ("get", "/api/v1/whatsapp/qr-raw"),
    ("post", "/api/v1/whatsapp/reset"),
    ("post", "/api/v1/whatsapp/pairing-code"),
]


class _ReponseFactice:
    status_code = 200
    text = "<html></html>"
    content = b"<svg/>"
    headers: dict = {}

    def json(self):
        return {"ok": True}


class _ClientFactice:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *a, **k):
        return _ReponseFactice()

    async def post(self, *a, **k):
        return _ReponseFactice()


@pytest.fixture
def service_factice(monkeypatch):
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _ClientFactice)


@pytest.mark.parametrize(("methode", "url"), ROUTES_ADMIN)
def test_appairage_sans_jeton_refuse(client, auth_headers, service_factice, methode, url):
    reponse = getattr(client, methode)(url, headers=auth_headers, **({"json": {}} if methode == "post" else {}))
    assert reponse.status_code == 401


@pytest.mark.parametrize(("methode", "url"), ROUTES_ADMIN)
def test_appairage_non_admin_refuse(client_sans_outils, auth_headers, service_factice, methode, url):
    reponse = getattr(client_sans_outils, methode)(
        url, headers=auth_headers, **({"json": {}} if methode == "post" else {})
    )
    assert reponse.status_code == 403


@pytest.mark.parametrize(("methode", "url"), ROUTES_ADMIN)
def test_appairage_admin_autorise(auth_client, auth_headers, service_factice, methode, url):
    reponse = getattr(auth_client, methode)(
        url, headers=auth_headers, **({"json": {}} if methode == "post" else {})
    )
    assert reponse.status_code == 200


def test_reset_en_get_interdit(auth_client, auth_headers, service_factice):
    assert auth_client.get("/api/v1/whatsapp/reset", headers=auth_headers).status_code == 405


def test_statut_exige_la_cle_api(client, auth_headers, service_factice):
    assert client.get("/api/v1/whatsapp/status").status_code == 401
    assert client.get("/api/v1/whatsapp/status", headers=auth_headers).status_code == 200


# --------------------------------------------------------------------------- #
# Périmètre des numéros entrants
# --------------------------------------------------------------------------- #


@pytest.fixture
def nova_db(monkeypatch):
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

    monkeypatch.setattr(whatsapp, "session_scope", scope)
    with scope() as db:
        db.add_all(
            [
                Utilisateur(telephone="+21611111111", nom_complet="Admin", is_admin=True, actif=True),
                Utilisateur(
                    telephone="+21622222222",
                    nom_complet="Chef",
                    is_admin=False,
                    actif=True,
                    outils_autorises=["situation_atelier"],
                ),
                Utilisateur(telephone="+21633333333", nom_complet="Parti", is_admin=False, actif=False),
            ]
        )
    yield scope
    engine.dispose()


@pytest.fixture
def liste_blanche(monkeypatch):
    def appliquer(valeur: str):
        monkeypatch.setenv("WHATSAPP_ALLOWED_NUMBERS", valeur)
        get_settings.cache_clear()

    yield appliquer
    get_settings.cache_clear()


def test_inconnu_liste_vide_refuse(nova_db, liste_blanche):
    liste_blanche("")
    autorise, _ = whatsapp._resoudre_acces("+21699999999")
    assert autorise is False


def test_inconnu_liste_vide_inbound_403(client, auth_headers, nova_db, liste_blanche):
    liste_blanche("")
    reponse = client.post(
        "/api/v1/whatsapp/inbound",
        headers=auth_headers,
        json={"from_number": "+21699999999", "message": "bonjour"},
    )
    assert reponse.status_code == 403


def test_inconnu_liste_blanche_lecture_seule(nova_db, liste_blanche):
    liste_blanche("+216 99 999 999")
    autorise, outils = whatsapp._resoudre_acces("+21699999999")
    assert autorise is True
    assert outils is not None
    assert sorted(outils) == sorted(READONLY_TOOL_NAMES)


def test_enregistre_garde_son_perimetre(nova_db, liste_blanche):
    liste_blanche("")
    assert whatsapp._resoudre_acces("+21622222222") == (True, ["situation_atelier"])
    assert whatsapp._resoudre_acces("+21611111111") == (True, None)
    assert whatsapp._resoudre_acces("+21633333333")[0] is False
