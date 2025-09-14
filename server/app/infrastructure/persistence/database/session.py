from __future__ import annotations
# server/app/infrastructure/persistence/database/session.py
"""Session SQLAlchemy & dépendances FastAPI."""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine() -> None:
    global _engine
    if _engine is None:
        # 1) Assure que l’URL contient connect_timeout (si absent)
        url = settings.DATABASE_URL
        if "connect_timeout=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}connect_timeout={settings.DB_CONNECT_TIMEOUT}"

        # 2) Passe aussi par connect_args (ceinture + bretelles)
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": settings.DB_CONNECT_TIMEOUT},
        )

def init_sessionmaker() -> None:
    global _SessionLocal
    if _SessionLocal is None:
        if _engine is None:
            init_engine()
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _ensure_sessionmaker() -> sessionmaker:
    if _SessionLocal is None:
        init_sessionmaker()
    assert _SessionLocal is not None
    return _SessionLocal


def get_session() -> Session:
    """Retourne une session (⚠️ à fermer manuellement)."""
    return _ensure_sessionmaker()()


@contextmanager
def get_sync_session() -> Iterator[Session]:
    """Context manager pratique pour `with get_sync_session() as s:`."""
    s = get_session()
    try:
        yield s
    finally:
        s.close()


# --- Dépendance FastAPI recommandée (auto-close) ------------------------------
def get_db() -> Iterator[Session]:
    """
    Dépendance FastAPI à utiliser comme:
      def endpoint(db: Session = Depends(get_db)): ...
    Ferme automatiquement la session.
    """
    db = get_session()
    try:
        yield db
    finally:
        db.close()
