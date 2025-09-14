# server/app/infrastructure/persistence/database/session.py
from __future__ import annotations

"""SQLAlchemy engine/session setup + FastAPI dependency."""

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def init_engine() -> Engine:
    """
    Create a singleton SQLAlchemy Engine, with dialect-aware connect_args.
    - PostgreSQL: pass connect_timeout
    - SQLite: share in-memory DB across connections (StaticPool), disable same-thread check
    """
    global _engine
    if _engine is not None:
        return _engine

    url = make_url(settings.DATABASE_URL)
    backend = url.get_backend_name()  # e.g. "postgresql+psycopg", "sqlite"
    kwargs: dict = dict(future=True, pool_pre_ping=True)
    connect_args: dict = {}

    if backend.startswith("postgresql") or backend == "postgres":
        # psycopg accepts connect_timeout (seconds)
        connect_args["connect_timeout"] = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
    elif backend.startswith("sqlite"):
        # SQLite: special flags; and for in-memory DB, use StaticPool to share connections
        connect_args["check_same_thread"] = False
        db_name = (url.database or "").strip()
        if db_name in ("", ":memory:"):
            kwargs["poolclass"] = StaticPool

    _engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, **kwargs)
    return _engine


def init_sessionmaker() -> sessionmaker:
    """Create (once) and return the SessionLocal factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=init_engine(),
            future=True,
            autoflush=True,
            expire_on_commit=False,
        )
    return _SessionLocal


def _ensure_sessionmaker() -> sessionmaker:
    sm = init_sessionmaker()
    assert sm is not None
    return sm


def get_session() -> Session:
    """Return a new Session (caller is responsible for closing it)."""
    return _ensure_sessionmaker()()


@contextmanager
def get_sync_session() -> Iterator[Session]:
    """Context manager: `with get_sync_session() as s:`"""
    s = get_session()
    try:
        yield s
    finally:
        s.close()


# FastAPI dependency (auto-close)
def get_db() -> Iterator[Session]:
    """
    Usage:
        def endpoint(db: Session = Depends(get_db)): ...
    """
    db = get_session()
    try:
        yield db
    finally:
        db.close()
