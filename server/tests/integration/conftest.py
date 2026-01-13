# server/tests/integration/conftest.py
# Conftest INTÉGRATION :
# - Pose les ENV côté HÔTE *avant* la collecte (pytest_configure) pour éviter
#   que l’app fige une mauvaise DATABASE_URL/REMINDER au moment de l’import.
# - Ne touche pas aux ENV dans les conteneurs (/.dockerenv présent) : ils
#   doivent parler à db:5432. L’ENV du conteneur est géré par docker-compose.
# - Ajoute une fixture "targets_base" qui détecte dynamiquement la bonne route.

from __future__ import annotations
import os
import pathlib
import sys
import importlib
import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Bootstrapping ENV avant la collecte
# ──────────────────────────────────────────────────────────────────────────────

def _in_container() -> bool:
    """Heuristique simple : fichier /.dockerenv présent => inside container."""
    return os.path.exists("/.dockerenv")


def _load_env_file_if_any() -> None:
    """
    Si ENV_FILE est défini côté hôte, charger ce .env (sans écraser l'existant).
    Utile pour forcer DATABASE_URL=...@localhost:5432/monitoring
    lors des tests d'intégration exécutés sur l'hôte.
    """
    env_file = os.getenv("ENV_FILE")
    if not env_file:
        return
    p = pathlib.Path(env_file)
    if not p.is_file():
        return

    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)


def pytest_configure(config) -> None:
    """
    S'exécute AVANT la collecte des tests (et donc avant l'import de modules).
    Parfait pour positionner des ENV qui doivent être vues par Settings() à l'import.
    """
    # Toujours activer le garde-fou d'intégration si absent
    os.environ.setdefault("INTEG_STACK_UP", os.getenv("INTEG_STACK_UP", "1"))

    if _in_container():
        # Dans les conteneurs, on NE TOUCHE PAS à DATABASE_URL / REMINDER.
        return

    # Côté HÔTE :
    # 1) Charger un éventuel fichier d'overrides (ENV_FILE=.env.integration.local)
    _load_env_file_if_any()

    # 2) Poser une DATABASE_URL par défaut (localhost:5432) si rien n'est défini
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/monitoring",
    )
    # 3) Raccourcir le cooldown pour les tests d’intégration (par défaut=1)
    os.environ.setdefault("ALERT_REMINDER_MINUTES", "1")

    # 4) IMPORTANT : si app.core.config a déjà été importé, le recharger
    if "app.core.config" in sys.modules:
        importlib.reload(sys.modules["app.core.config"])

    # 5) Celery: exécution synchrone (eager) + broker/result en mémoire
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
    os.environ.setdefault("CELERY_BROKER_URL", "memory://")
    os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

    # Recharger l’app Celery pour qu’elle lise les ENV ci-dessus
    try:
        import app.workers.celery_app as celery_app_mod
        importlib.reload(celery_app_mod)
        celery = getattr(celery_app_mod, "celery", None)
        if celery:
            celery.conf.task_always_eager = True
            celery.conf.task_eager_propagates = True
            celery.conf.broker_url = os.environ["CELERY_BROKER_URL"]
            celery.conf.result_backend = os.environ["CELERY_RESULT_BACKEND"]
            # optionnel : pas de stockage de résultats
            celery.conf.task_ignore_result = True
    except Exception as e:
        print(f"[integration conftest] Celery eager patch skipped: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Détection dynamique de la route targets
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def targets_base(session_retry, api_base, api_headers) -> str:
    """
    Détecte la bonne route 'targets' en tentant plusieurs candidats et via l'OpenAPI si besoin.
    Retourne un chemin commençant par '/api/v1/...'.
    """
    candidates = [
        "/api/v1/targets",
        "/api/v1/http-targets",
        "/api/v1/http_targets",
    ]

    # 1) Tentatives directes (OPTIONS puis GET)
    for c in candidates:
        url = f"{api_base}{c}"
        r = session_retry.options(url, headers=api_headers, timeout=5)
        if r.status_code in (200, 204, 401, 403, 405):
            return c
        r = session_retry.get(url, headers=api_headers, timeout=5)
        if r.status_code in (200, 401, 403, 405):
            return c

    # 2) Lecture de l'OpenAPI si exposée
    r = session_retry.get(f"{api_base}/openapi.json", headers=api_headers, timeout=5)
    if r.ok:
        paths = r.json().get("paths", {})
        for c in candidates:
            if c in paths:
                return c
        # Fallback : heuristique
        for p in paths.keys():
            if "target" in p:
                return p

    pytest.fail("Impossible de localiser la route des 'targets'. Vérifie le prefix router.")
