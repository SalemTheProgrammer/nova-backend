"""Database engine, session factory, and FastAPI dependency."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

settings = get_settings()

# `check_same_thread=False` is required for SQLite under FastAPI's threadpool.
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    # SQLite connections are cheap (file handles) : un pool large absorbe les
    # rafales du dashboard sans TimeoutError — l'incident prod du 18/07 a montré
    # que 5+10 connexions saturaient en quelques minutes de trafic soutenu.
    pool_size=20,
    max_overflow=30,
    pool_timeout=10,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Enforce foreign keys on SQLite (off by default), plus concurrency pragmas.

    WAL permet lecteurs et écrivain simultanés (le dashboard lit pendant que le
    simulateur écrit) ; busy_timeout fait patienter au lieu de lever
    « database is locked » quand deux écritures se croisent.
    """
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session, committing on success."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager yielding a session, committing on success (for agent tools/scripts)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Safe to call repeatedly (idempotent)."""
    from app import models  # noqa: F401  (ensures models are imported/registered)
    from app.db.base import Base

    Base.metadata.create_all(bind=engine)
    logger.info("db_initialized", url=settings.database_url)
