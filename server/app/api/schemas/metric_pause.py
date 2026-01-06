from __future__ import annotations
"""server/app/api/schema/metric_pause.py
~~~~~~~~~~~~~~~~~~~~~~~~
Schema pour Pause/Unpause d'une métrique.
"""

from pydantic import BaseModel


class TogglePauseIn(BaseModel):
    paused: bool
