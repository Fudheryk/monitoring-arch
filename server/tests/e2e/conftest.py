# server/tests/e2e/conftest.py
# -------------------------------------------------------------------
# Conftest E2E MINIMAL :
# - Ne redéclare PAS d’options pytest (--api / --api-key).
# - Ne duplique PAS les fixtures communes (api_base, api_headers,
#   session_retry, wait) : elles viennent du conftest GLOBAL
#   => server/tests/conftest.py
# - Pose uniquement un défaut pour le garde-fou E2E_STACK_UP afin
#   d'éviter les SKIP en local quand la stack tourne déjà.
# -------------------------------------------------------------------

from __future__ import annotations

import os
import pytest


@pytest.fixture(scope="session", autouse=True)
def _set_e2e_env_defaults() -> None:
    # Laisse la CI/ton shell surcharger si besoin.
    # Par défaut on *active* le flag en E2E pour éviter les SKIP.
    os.environ.setdefault("E2E_STACK_UP", os.getenv("E2E_STACK_UP", "1"))
