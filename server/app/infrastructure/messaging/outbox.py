# server/app/infrastructure/messaging/outbox.py
from __future__ import annotations
"""
Service Outbox : API haut-niveau au-dessus du repository
- save_event() : création d'un évènement d’outbox
- due_events() : sélection des évènements "dûs" à livrer
- mark_delivering() / mark_delivered() / schedule_retry() / mark_failed()

⚠️ IMPORTANT :
- NE PAS importer ce module depuis lui-même (aucun `from ...outbox import Outbox` ici).
- Les backoffs sont lus depuis les settings mais tolèrent aussi une simple liste/CSV en ENV.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
import math
import os
import random

from app.core.config import settings
from app.infrastructure.persistence.repositories.outbox_repository import OutboxRepository


# ──────────────────────────────────────────────────────────────────────────────
# Helpers backoff + jitter
# ──────────────────────────────────────────────────────────────────────────────

def _parse_backoffs() -> list[int]:
    """
    Accepte :
      - settings.OUTBOX_BACKOFFS (list[int] OU "30,120,600")
      - variable d'env OUTBOX_BACKOFFS idem
    Fallback par défaut : [30, 120, 600]
    """
    raw: Any = getattr(settings, "OUTBOX_BACKOFFS", None) or os.environ.get("OUTBOX_BACKOFFS")
    if isinstance(raw, (list, tuple)):
        try:
            return [int(x) for x in raw]  # pydantic peut déjà donner une liste
        except Exception:
            return [30, 120, 600]
    if not raw:
        return [30, 120, 600]
    try:
        return [int(x.strip()) for x in str(raw).split(",") if x.strip()]
    except Exception:
        return [30, 120, 600]


def _jitter(seconds: int, pct: float) -> int:
    """Applique un jitter symétrique ±pct, borne à [0..0.9]."""
    pct = max(0.0, min(float(pct), 0.9))
    low = seconds * (1.0 - pct)
    high = seconds * (1.0 + pct)
    return int(math.ceil(random.uniform(low, high)))


# ──────────────────────────────────────────────────────────────────────────────
# Service Outbox
# ──────────────────────────────────────────────────────────────────────────────

class Outbox:
    def __init__(self, repo: OutboxRepository):
        self.repo = repo
        self._backoffs: list[int] = _parse_backoffs()
        self._jitter_pct: float = float(getattr(settings, "OUTBOX_JITTER_PCT", 0.2) or 0.2)

    # --- Écriture -------------------------------------------------------------

    def save_event(
        self,
        *,
        type_: str,
        payload: Mapping[str, Any],
        client_id: str | None,
        incident_id: str | None = None,
        next_attempt_at: datetime | None = None,
    ):
        """Crée un évènement outbox."""
        return self.repo.insert(
            type_=type_,
            payload=dict(payload),
            client_id=client_id,
            incident_id=incident_id,
            next_attempt_at=next_attempt_at,
        )

    # --- Lecture --------------------------------------------------------------

    def due_events(self, *, limit: int = 100, as_of: datetime | None = None):
        """
        Retourne les évènements dûs à livrer à l’instant `as_of` (UTC now par défaut).
        Délègue au repo (qui filtre sur status et next_attempt_at <= as_of).
        """
        as_of = as_of or datetime.now(timezone.utc)
        return self.repo.fetch_due(limit=limit, as_of=as_of)

    # --- Transitions d’état ---------------------------------------------------

    def mark_delivering(self, event_id: str) -> int:
        """
        Passe l’event en DELIVERING et incrémente attempts.
        Retourne la nouvelle valeur de attempts.
        """
        return self.repo.mark_delivering(event_id)

    def schedule_retry(self, event_id: str, *, attempts_done: int):
        """
        Programme un retry avec backoff + jitter en fonction du nombre de tentatives déjà faites.
        attempts_done = valeur retournée par mark_delivering()
        """
        # index dans la grille (0 pour 1ère tentative, clamp à la fin)
        idx = min(max(attempts_done - 1, 0), len(self._backoffs) - 1)
        base = self._backoffs[idx] if self._backoffs else 30
        delay = _jitter(base, self._jitter_pct)
        when = datetime.now(timezone.utc) + timedelta(seconds=delay)
        self.repo.mark_retry(event_id, when)

    def mark_delivered(self, event_id: str, receipt: Mapping[str, Any] | None = None):
        self.repo.mark_delivered(event_id, dict(receipt or {}))

    def mark_failed(self, event_id: str, reason: str):
        self.repo.mark_failed(event_id, reason)
