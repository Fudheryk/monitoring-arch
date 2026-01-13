from __future__ import annotations
"""
server/app/infrastructure/persistence/database/base.py

Base ORM SQLAlchemy 2.x.

Le `from app.infrastructure.persistence.database.models import *` ci-dessous
est volontaire : il “remplit” Base.metadata avec TOUTES les tables.
Ainsi n’importe quel `Base.metadata.create_all(bind=engine)` (p.ex. en SQLite
pendant les tests) créera le schéma complet.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarative pour tous les modèles."""
    pass


# Effet de bord voulu : en important ce package on enregistre toutes les tables.
# Les noqa évitent les warnings “unused import”.
from app.infrastructure.persistence.database.models import *  # noqa: F403,F401

__all__ = ["Base"]
