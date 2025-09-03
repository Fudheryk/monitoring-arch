from __future__ import annotations
"""server/app/infrastructure/persistence/database/session.py
~~~~~~~~~~~~~~~~~~~~~~~~
Session SQLAlchemy.
"""
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from app.core.config import settings

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine = None
_SessionLocal: sessionmaker | None = None

def init_engine() -> None:
    global _engine
    if _engine is None:
        _engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

def init_sessionmaker() -> None:
    global _SessionLocal
    if _SessionLocal is None:
        if _engine is None:
            init_engine()
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

def get_session() -> Session:
    if _SessionLocal is None:
        init_engine()
        init_sessionmaker()
    assert _SessionLocal is not None
    return _SessionLocal()  # type: ignore[call-arg]

@contextmanager
def get_sync_session() -> Session:
    s = get_session()
    try:
        yield s
    finally:
        s.close()
