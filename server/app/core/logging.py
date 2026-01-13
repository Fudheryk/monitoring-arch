# server/app/core/logging.py
from __future__ import annotations
import logging, os
import json
import sys


def _level_from_env(default: int = logging.INFO) -> int:
    name = os.getenv("LOG_LEVEL", "").upper().strip()
    if not name:
        return default
    return getattr(logging, name, default)


def setup_logging(level: int | str | None = None) -> None:
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    if level is None:
        level = _level_from_env(logging.INFO)
    
    # Format JSON en prod, texte en dev
    env = os.getenv("ENVIRONMENT", "development")
    
    if env == "production":
        # Format JSON pour parsing facile
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logging.basicConfig(
            level=level,
            handlers=[handler],
            force=True,
        )
    else:
        # Format texte lisible en dev
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            force=True,
        )
    
    # Réduire verbosité de librairies tierces en prod
    if env == "production":
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("celery.app.trace").setLevel(logging.INFO)

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)