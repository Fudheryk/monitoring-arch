from __future__ import annotations
"""
/server/app/infrastructure/persistence/database/session.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Init Engine/Session + helpers (compat FastAPI + services).

- get_db()       : dépendance FastAPI (yield Session) avec commit/rollback.
- open_session() : context manager pour services/tests (with ... as s:) idem.

SQLite :
- StaticPool pour in-memory, check_same_thread=False.
- Création du schéma si http_targets manquante (tests unitaires sans Alembic).
"""

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.infrastructure.persistence.database.base import Base  # metadata

DATABASE_URL = settings.DATABASE_URL

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _ensure_sqlite_schema(engine: Engine) -> None:
    """En SQLite, crée le schéma minimal si Alembic n'a pas tourné (ex: tests)."""
    if engine.url.get_backend_name() != "sqlite":
        return
    try:
        insp = inspect(engine)
        if not insp.has_table("http_targets"):
            Base.metadata.create_all(bind=engine)
    except Exception:
        Base.metadata.create_all(bind=engine)


def init_engine() -> Engine:
    """Construit l'Engine (singleton module)."""
    global _engine
    if _engine is not None:
        return _engine

    url = make_url(DATABASE_URL)
    kwargs: dict = dict(pool_pre_ping=True, future=True)
    connect_args: dict = {}
    if url.get_backend_name().startswith("sqlite"):
        connect_args["check_same_thread"] = False
        db_name = (url.database or "").strip()
        # Partage de connexion pour :memory:
        if db_name in ("", ":memory:"):
            kwargs["poolclass"] = StaticPool

    _engine = create_engine(DATABASE_URL, connect_args=connect_args, **kwargs)
    _ensure_sqlite_schema(_engine)
    return _engine


def init_sessionmaker() -> sessionmaker:
    """Sessionmaker partagé (singleton module)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=init_engine(),
            future=True,
            autoflush=True,
            expire_on_commit=False,
        )
    return _SessionLocal


def get_session() -> Session:
    """Nouvelle Session synchrone (à fermer par l’appelant)."""
    return init_sessionmaker()()


# -------- Context manager (services/tests) ------------------------------------

@contextmanager
def open_session() -> Iterator[Session]:
    """
    Usage (services/tests) :
        with open_session() as s:
            ...
    Commit/rollback automatiques.
    """
    s = get_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# -------- Dépendance FastAPI --------------------------------------------------

def get_db() -> Iterator[Session]:
    """
    Usage FastAPI :
        def endpoint(s: Session = Depends(get_db)): ...
    """
    s = get_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
