from __future__ import annotations
"""server/app/core/logging.py
~~~~~~~~~~~~~~~~~~~~~~~~
Configuration logs.
"""
import logging


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
