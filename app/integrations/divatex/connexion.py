"""Connexion à la base DIVA (divatex) en lecture seule.

Seul point d'accès SQL vers divatex : paramètres toujours liés (``:nom``).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from functools import wraps
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import InterfaceError, OperationalError

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

_MESSAGE_INDISPONIBLE = "Base DIVA injoignable."
_TTL_DATE_REFERENCE_S = 600.0

_engine: Engine | None = None
_verrou = threading.Lock()
_caches: list[Callable[[], None]] = []


class DivatexIndisponible(AppError):
    status_code = 503
    code = "divatex_indisponible"

    def __init__(
        self, message: str = _MESSAGE_INDISPONIBLE, *, details: dict | None = None
    ) -> None:
        super().__init__(message, details=details)


def _creer_engine(url: str) -> Engine:
    settings = get_settings()
    options: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 1800}
    if url.startswith("mysql"):
        options.update(pool_size=5, max_overflow=5, connect_args={"connect_timeout": 5})
    engine = create_engine(url, **options)

    @event.listens_for(engine, "connect")
    def _lecture_seule(dbapi_conn, _record) -> None:  # pragma: no cover - MariaDB réel
        if engine.dialect.name != "mysql":
            return
        timeout = int(settings.divatex_statement_timeout_s)
        cur = dbapi_conn.cursor()
        try:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(f"SET SESSION max_statement_time = {timeout}")
        finally:
            cur.close()

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    with _verrou:
        if _engine is None:
            url = get_settings().divatex_url
            if not url:
                raise DivatexIndisponible("Base DIVA non configurée (DIVATEX_URL).")
            _engine = _creer_engine(url)
        return _engine


def set_engine(engine: Engine | None) -> None:
    """Injecte un moteur (tests) ; vide aussi les caches."""
    global _engine
    with _verrou:
        _engine = engine
    vider_caches()


@contextmanager
def connexion() -> Iterator[Connection]:
    try:
        with get_engine().connect() as conn:
            yield conn
    except (OperationalError, InterfaceError) as exc:
        logger.warning("divatex_indisponible", erreur=type(exc).__name__)
        raise DivatexIndisponible() from exc


def requete(sql: str, **params: Any) -> list[dict]:
    with connexion() as conn:
        resultat = conn.execute(text(sql), params)
        return [dict(ligne) for ligne in resultat.mappings()]


def cache(ttl_s: float | None = None) -> Callable:
    """Mémoïsation à durée de vie limitée, clé = arguments."""

    def decorateur(fonction: Callable) -> Callable:
        magasin: dict[Any, tuple[float, Any]] = {}

        @wraps(fonction)
        def enveloppe(*args: Any, **kwargs: Any) -> Any:
            ttl = get_settings().divatex_cache_ttl_s if ttl_s is None else ttl_s
            cle = (args, tuple(sorted(kwargs.items())))
            maintenant = time.monotonic()
            trouve = magasin.get(cle)
            if trouve is not None and maintenant - trouve[0] < ttl:
                return trouve[1]
            valeur = fonction(*args, **kwargs)
            magasin[cle] = (maintenant, valeur)
            return valeur

        enveloppe.vider = magasin.clear  # type: ignore[attr-defined]
        _caches.append(magasin.clear)
        return enveloppe

    return decorateur


def vider_caches() -> None:
    for vider in _caches:
        vider()


@cache(ttl_s=_TTL_DATE_REFERENCE_S)
def _derniere_date_lecture() -> date:
    lignes = requete(
        "SELECT Date AS d FROM lectures "
        "WHERE IDLectures = (SELECT MAX(IDLectures) FROM lectures)"
    )
    if not lignes or lignes[0]["d"] is None:
        raise DivatexIndisponible("Aucune lecture dans la base DIVA.")
    valeur = lignes[0]["d"]
    if isinstance(valeur, str):
        return date.fromisoformat(valeur[:10])
    return valeur.date() if hasattr(valeur, "date") else valeur


def date_reference() -> date:
    """« Aujourd'hui » pour toutes les réponses : jamais ``date.today()`` ailleurs."""
    reglage = get_settings().divatex_date_reference.strip().lower()
    if reglage == "today":
        return date.today()
    if reglage == "auto":
        return _derniere_date_lecture()
    return date.fromisoformat(reglage)
