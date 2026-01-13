# server/app/workers/tasks/outbox_tasks.py
from __future__ import annotations
from datetime import datetime, timezone
import logging
from typing import Any

from app.workers.celery_app import celery
from app.infrastructure.messaging.outbox import Outbox
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.repositories.outbox_repository import OutboxRepository

logger = logging.getLogger(__name__)

def _deliver_one(payload: dict[str, Any]) -> bool:
    try:
        return not bool(payload.get("_force_fail"))
    except Exception:
        return False

def deliver_outbox_batch(limit: int = 100) -> int:
    """
    Livre un batch d'évènements Outbox "dus" (next_attempt_at <= now) en 2 phases :

    Phase 1 (CLAIM) :
      - On récupère les évènements dus.
      - Pour chacun, on tente de le "claim" (mark_delivering) => status=DELIVERING + attempts++.
      - IMPORTANT: dans l'implémentation actuelle, mark_delivering() COMMIT déjà côté repository,
        donc le claim est effectué "event par event" (pas un commit batch).
      - On sort uniquement avec des PRIMITIVES (id, payload) pour ne pas dépendre d'objets ORM détachés.

    Phase 2 (DELIVERY) :
      - Pour chaque évènement claimé :
          - on exécute le "deliver" (ici _deliver_one(payload) simulé).
          - si OK  -> mark_delivered(...)
          - sinon  -> schedule_retry(..., attempts_done=attempts)
        Chaque event est finalisé dans une session dédiée (robuste aux erreurs event par event).

    Politique "strict safe" :
      - On ne claim que les events PENDING (pas ceux déjà DELIVERING),
        tant qu'on n'a pas de mécanisme explicite de lease/timeout sur DELIVERING.

    Retour :
      - nombre d'évènements effectivement marqués DELIVERED.
    """

    now = datetime.now(timezone.utc)

    # ---------------------------------------------------------------------
    # Phase 1 : claim + extraction primitives-only
    # ---------------------------------------------------------------------
    claimed: list[dict[str, Any]] = []
    attempts_by_id: dict[str, int] = {}

    with open_session() as s:
        ob = Outbox(OutboxRepository(s))

        events = ob.due_events(limit=limit, as_of=now)

        for ev in events:
            eid = str(ev.id)

            # mark_delivering commit déjà (dans ton repo actuel)
            attempts = ob.mark_delivering(eid)
            if not attempts:
                continue

            attempts_by_id[eid] = attempts

            # primitives-only
            claimed.append({"id": eid, "payload": (ev.payload or {})})

    # ---------------------------------------------------------------------
    # Phase 2 : delivery + finalisation (1 session par event)
    # ---------------------------------------------------------------------
    delivered_count = 0

    for item in claimed:
        eid = item["id"]
        payload = item["payload"]

        try:
            ok = _deliver_one(payload)

            with open_session() as s:
                ob = Outbox(OutboxRepository(s))
                if ok:
                    ob.mark_delivered(eid, receipt={"ok": True})
                    delivered_count += 1
                else:
                    ob.schedule_retry(eid, attempts_done=attempts_by_id.get(eid, 0))

        except Exception:
            logger.exception("outbox: error while delivering event_id=%s", eid)

            # dernier recours: tenter de replanifier pour ne pas bloquer
            try:
                with open_session() as s:
                    ob = Outbox(OutboxRepository(s))
                    ob.schedule_retry(eid, attempts_done=attempts_by_id.get(eid, 0))
            except Exception:
                logger.exception("outbox: failed to schedule retry for event_id=%s", eid)

    return delivered_count


@celery.task(name="outbox.deliver")
def deliver_outbox_batch_task(limit: int = 100) -> int:
    return deliver_outbox_batch(limit)
