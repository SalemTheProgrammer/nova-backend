"""Connexion DIVA en lecture seule, date de référence et cache."""
from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.integrations.divatex.connexion import (
    DivatexIndisponible,
    cache,
    date_reference,
    get_engine,
    requete,
    set_engine,
    vider_caches,
)

_URL_REELLE = os.environ.get("DIVATEX_URL")  # lue avant le nettoyage par les fixtures


@pytest.fixture
def engine_fixture():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE lectures (IDLectures INTEGER PRIMARY KEY, Date TEXT)"))
        conn.execute(
            text("INSERT INTO lectures (IDLectures, Date) VALUES (1, '2026-08-27'), "
                 "(2, '2026-08-28'), (3, '2026-08-28')")
        )
    set_engine(engine)
    yield engine
    set_engine(None)
    engine.dispose()


@pytest.fixture(autouse=True)
def _reglages(monkeypatch):
    monkeypatch.delenv("DIVATEX_DATE_REFERENCE", raising=False)
    monkeypatch.delenv("DIVATEX_URL", raising=False)
    get_settings.cache_clear()
    vider_caches()
    yield
    set_engine(None)
    get_settings.cache_clear()


def test_date_reference_auto(engine_fixture):
    assert date_reference() == date(2026, 8, 28)


def test_date_reference_fixe(monkeypatch):
    monkeypatch.setenv("DIVATEX_DATE_REFERENCE", "2026-08-20")
    get_settings.cache_clear()
    vider_caches()
    assert date_reference() == date(2026, 8, 20)


def test_date_reference_today(monkeypatch):
    monkeypatch.setenv("DIVATEX_DATE_REFERENCE", "today")
    get_settings.cache_clear()
    vider_caches()
    assert date_reference() == date.today()


def test_parametres_lies(engine_fixture):
    lignes = requete("SELECT COUNT(*) n FROM lectures WHERE Date = :d", d="2026-08-28")
    assert lignes[0]["n"] == 2


def test_connexion_erreur_traduite():
    set_engine(create_engine("mysql+pymysql://x:y@127.0.0.1:1/none"))
    with pytest.raises(DivatexIndisponible) as exc:
        requete("SELECT 1")
    assert exc.value.status_code == 503
    assert exc.value.code == "divatex_indisponible"
    assert exc.value.message == "Base DIVA injoignable."


def test_url_vide_indisponible():
    with pytest.raises(DivatexIndisponible):
        get_engine()


def test_cache_ttl():
    appels = []

    @cache(ttl_s=60)
    def f(x):
        appels.append(x)
        return x

    f(1)
    f(1)
    assert appels == [1]
    f(2)
    assert appels == [1, 2]
    f.vider()
    f(1)
    assert appels == [1, 2, 1]


@pytest.mark.divatex
def test_ecriture_refusee_sur_divatex_reel(monkeypatch):
    from sqlalchemy.exc import DBAPIError

    if not _URL_REELLE:
        pytest.skip("DIVATEX_URL non défini")
    monkeypatch.setenv("DIVATEX_URL", _URL_REELLE)
    get_settings.cache_clear()
    with pytest.raises((DBAPIError, DivatexIndisponible)):
        requete("UPDATE alea SET Alea = Alea WHERE IDAlea = -1")
