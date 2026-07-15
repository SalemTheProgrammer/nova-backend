"""Filesystem paths derived from settings."""
from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


def chat_history_db_path() -> str:
    """SQLite file backing the agent's conversation checkpointer.

    Kept alongside (but separate from) the main app database so that
    conversation history has its own lifecycle — e.g. resetting demo data in
    `database_url` doesn't silently wipe chat threads too.
    """
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        app_db = Path(settings.database_url.split("sqlite:///", 1)[-1])
        return str(app_db.with_name("nova_chat_history.db"))
    return "./nova_chat_history.db"
