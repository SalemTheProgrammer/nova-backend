"""Base de données propre à Nova (SQLite par défaut, MariaDB en production)."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect

from app.core.config import Settings, get_settings
from app.core.paths import chat_history_db_path
from app.db.base import Base


def test_url_masquee():
    s = Settings(database_url="mysql+pymysql://nova_app:secret@db:3306/nova")
    assert "secret" not in s.database_url_masquee and "***" in s.database_url_masquee


def test_url_masquee_sqlite_inchangee():
    s = Settings(database_url="sqlite:///./nova.db")
    assert s.database_url_masquee == "sqlite:///./nova.db"


def test_chemin_historique_non_sqlite(monkeypatch):
    monkeypatch.delenv("CHAT_HISTORY_DB_PATH", raising=False)
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@h/nova")
    get_settings.cache_clear()
    try:
        assert chat_history_db_path().endswith("nova_chat_history.db")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_chemin_historique_explicite(monkeypatch):
    monkeypatch.setenv("CHAT_HISTORY_DB_PATH", "/tmp/hist.db")
    get_settings.cache_clear()
    try:
        assert chat_history_db_path() == "/tmp/hist.db"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


@pytest.mark.mariadb
def test_creation_tables_mariadb():  # NOVA_TEST_MARIADB_URL pointe vers une base vide
    from app import models  # noqa: F401

    url = os.environ.get("NOVA_TEST_MARIADB_URL")
    if not url:
        pytest.skip("NOVA_TEST_MARIADB_URL non défini")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    assert {
        "utilisateurs",
        "audit_log",
        "document_rag",
        "envoi_planifie",
        "codes_verification",
    } <= set(inspect(engine).get_table_names())
