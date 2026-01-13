# server/tests/integration/_dbutils.py
# Outils communs pour les tests d'intégration (DB).
# - require_db_or_skip() : skippe proprement si la DB d'intégration n'est pas joignable.

from __future__ import annotations
import os
import time
import pytest


def _dsn_candidates() -> list[str]:
    """
    DSN possibles, du plus spécifique au plus générique.
    - DATABASE_URL (utilisé par l'app)
    - PG_DSN (optionnel pour les tests)
    - localhost:5432/monitoring (par défaut docker-compose avec `ports: 5432:5432`)
    """
    cands = []
    if os.getenv("DATABASE_URL"):
        cands.append(os.getenv("DATABASE_URL"))  # ex: postgresql+psycopg://postgres:postgres@localhost:5432/monitoring
    if os.getenv("PG_DSN"):
        cands.append(os.getenv("PG_DSN"))
    # fallback raisonnable en local
    cands.append("postgresql://postgres:postgres@localhost:5432/monitoring")
    return cands


def _try_connect_psycopg(dsn: str) -> bool:
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        return True
    except Exception:
        return False


def require_db_or_skip(wait_seconds: int = 0) -> None:
    """
    Vérifie que la DB est accessible ; sinon SKIP les tests d'intégration appelants.

    - Respecte le garde-fou INTEG_STACK_UP : si absent/≠"1", on SKIP immédiatement.
    - Essaie plusieurs DSN (voir _dsn_candidates()).
    - Optionnel : attend `wait_seconds` (polling rapide) avant de skip.
    """
    if os.getenv("INTEG_STACK_UP", "") != "1":
        pytest.skip("Integration stack not running (export INTEG_STACK_UP=1)", allow_module_level=True)

    deadline = time.time() + max(0, wait_seconds)
    cands = _dsn_candidates()

    while True:
        for dsn in cands:
            if _try_connect_psycopg(dsn):
                return  # OK : DB accessible
        if time.time() >= deadline:
            break
        time.sleep(1)

    pytest.skip(
        "Integration DB not reachable. "
        "Set DATABASE_URL/PG_DSN or start docker compose (db exposed on localhost:5432).",
        allow_module_level=True,
    )
