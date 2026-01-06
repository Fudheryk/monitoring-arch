# server/app/core/logging.py
from __future__ import annotations
import logging, os

def _level_from_env(default: int = logging.INFO) -> int:
    name = os.getenv("LOG_LEVEL", "").upper().strip()
    if not name:
        return default
    return getattr(logging, name, default)

def setup_logging(level: int | str | None = None) -> None:
    """
    Configure le logging global.
    - LOG_LEVEL dans l'env (DEBUG/INFO/WARNING/ERROR) si level=None
    - force=True pour écraser toute config existante (handlers, niveaux)
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    if level is None:
        level = _level_from_env(logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,                     # <- clé pour tout écraser
    )

    # Harmonise quelques loggers fréquents
    for name in (
        "uvicorn", "uvicorn.access", "uvicorn.error",
        "celery", "celery.app.trace",
        "app",  # 👈 ajoute ceci pour forcer app.* en DEBUG
    ):
        logging.getLogger(name).setLevel(level)
