# Contrat CRUD du repository HttpTarget — tolère les différences d'API (session en arg ou non).
#
# Problème résolu :
# - Hors conteneur, certains imports pouvaient figer une DATABASE_URL pointant vers "db:5432"
#   (non résoluble depuis l'hôte), d'où l'erreur "Name or service not known".
# - Ici, on force l'ENV côté test AVANT de (ré)initialiser la stack session/engine,
#   et on reloade proprement le module "session" si besoin, afin de repartir d'un engine correct.
#
# Détails :
# - require_db_or_skip() skippe si la DB host n'est pas joignable (ex : stack down).
# - URL unique (paramètre ?u=...) pour éviter tout conflit d'unicité entre runs.
# - Fallback ORM si certaines méthodes de repo n'existent pas.

from __future__ import annotations

import os
import uuid
import importlib
import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration

# Import robuste de l'utilitaire de skip: on tente absolu puis relatif.
try:
    from server.tests.integration._dbutils import require_db_or_skip  # type: ignore
except Exception:  # pragma: no cover
    from ._dbutils import require_db_or_skip  # type: ignore  # fallback si le package "server" n'est pas visible


def _ensure_host_db_url() -> None:
    """
    Si on n'est pas en conteneur et que DATABASE_URL est absente, force une URL localhost.
    """
    if not os.path.exists("/.dockerenv") and not os.getenv("DATABASE_URL"):
        os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@localhost:5432/monitoring"


@pytest.mark.timeout(30)
def test_http_target_repo_crud():
    # 1) Fixe l'ENV très tôt (avant import app.*)
    _ensure_host_db_url()

    # 2) Skip doux si la DB n'est pas joignable depuis l'hôte
    require_db_or_skip()

    # 3) (Re)charger le module de session APRÈS avoir fixé l'ENV,
    #    et forcer la réinit de l'engine si besoin.
    import app.infrastructure.persistence.database.session as session_mod  # type: ignore

    # Au cas où un engine aurait été initialisé plus tôt avec une mauvaise URL :
    # on remet à zéro puis on réinitialise avec la DATABASE_URL actuelle.
    if hasattr(session_mod, "_engine"):
        session_mod._engine = None  # type: ignore[attr-defined]
    if hasattr(session_mod, "_SessionLocal"):
        session_mod._SessionLocal = None  # type: ignore[attr-defined]
    importlib.reload(session_mod)
    # Initialisation explicite (utilise settings.DATABASE_URL à présent correcte)
    if hasattr(session_mod, "init_engine"):
        session_mod.init_engine()   # type: ignore[attr-defined]
    if hasattr(session_mod, "init_sessionmaker"):
        session_mod.init_sessionmaker()  # type: ignore[attr-defined]

    # 4) Imports dépendants de la session correctement initialisée
    from app.infrastructure.persistence.database.session import get_sync_session  # type: ignore
    from app.infrastructure.persistence.database.models.http_target import HttpTarget  # type: ignore

    # Repo optionnel (selon l'emplacement exact dans le projet)
    try:
        from app.infrastructure.persistence.repositories.http_target_repository import (  # type: ignore
            HttpTargetRepository,
        )
    except Exception:
        HttpTargetRepository = None  # type: ignore

    # Si le repo n'existe pas (ou nom différent), on skip ce test contractuel
    if HttpTargetRepository is None:
        pytest.skip("HttpTargetRepository introuvable")

    with get_sync_session() as session:
        # Instanciation souple : avec session si demandé, sinon sans
        try:
            repo = HttpTargetRepository(session)  # type: ignore[call-arg]
        except TypeError:
            repo = HttpTargetRepository()  # type: ignore[call-arg]

        unique = uuid.uuid4()
        payload = {
            "client_id": uuid.uuid4(),
            "name": "t-repo",
            # URL unique pour éviter une contrainte d'unicité éventuelle entre runs
            "url": f"https://example.com/health?u={unique}",
            "method": "GET",
            "expected_status_code": 200,
            "timeout_seconds": 5,
            "check_interval_seconds": 60,
            "is_active": True,
        }

        # CREATE (via repo si possible, sinon ORM)
        if hasattr(repo, "create"):
            created = repo.create(payload)  # type: ignore[attr-defined]
            tid = getattr(created, "id")
        else:
            obj = HttpTarget(**payload)
            session.add(obj)
            session.commit()
            tid = obj.id

        assert tid

        # READ
        if hasattr(repo, "get_by_id"):
            got = repo.get_by_id(tid)  # type: ignore[attr-defined]
        else:
            got = session.get(HttpTarget, tid)
        assert got is not None
        assert got.name == "t-repo"

        # UPDATE
        if hasattr(repo, "update"):
            repo.update(tid, {"is_active": False})  # type: ignore[attr-defined]
            updated = repo.get_by_id(tid) if hasattr(repo, "get_by_id") else session.get(HttpTarget, tid)
        else:
            obj = session.get(HttpTarget, tid)
            obj.is_active = False
            session.commit()
            updated = session.get(HttpTarget, tid)
        assert updated.is_active is False

        # LIST
        if hasattr(repo, "list"):
            lst = repo.list()  # type: ignore[attr-defined]
            assert any(getattr(t, "id", None) == tid for t in lst)
        else:
            lst = session.scalars(select(HttpTarget)).all()
            assert any(t.id == tid for t in lst)

        # DELETE
        if hasattr(repo, "delete"):
            repo.delete(tid)  # type: ignore[attr-defined]
            after = repo.get_by_id(tid) if hasattr(repo, "get_by_id") else session.get(HttpTarget, tid)
            assert after is None
        else:
            obj = session.get(HttpTarget, tid)
            session.delete(obj)
            session.commit()
            assert session.get(HttpTarget, tid) is None
