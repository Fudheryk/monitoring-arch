# coding: utf-8
# /!usr/bin/env python3
# server/app/core/utils/datetime.py
"""server/app/core/utils/datetime.py
~~~~~~~~~~~~~~~~~~~~~~~~
Utilitaires pour la gestion des dates et heures.
"""

from datetime import datetime, timezone
from typing import Optional

def age_in_seconds(dt: Optional[datetime]) -> Optional[int]:
    """Retourne l’âge (en secondes) d’un datetime UTC, ou None si absent."""
    if not dt:
        return None
    now = datetime.now(timezone.utc)
    delta = now - dt
    return int(delta.total_seconds())
