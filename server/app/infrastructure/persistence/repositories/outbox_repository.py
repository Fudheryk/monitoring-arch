# server/app/infrastructure/persistence/repositories/outbox_repository.py
from __future__ import annotations
"""
Repository Outbox : opérations CRUD bas niveau.

Points clés :
- `fetch_due(..., as_of=...)` : pivot temporel paramétrable (par défaut: now UTC),
  compatible avec les appels existants (on accepte aussi `now=` en rétro-compat).
- On considère les évènements à traiter avec status IN (PENDING, DELIVERING).
- Conversions UUID robustes pour accepter str/uuid.UUID.
- Mises à jour de `updated_at` lors des changements d’état.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.outbox_event import (
    OutboxEvent,
    OutboxStatus,
)


def _coerce_uuid(v: str | uuid.UUID | None) -> uuid.UUID | None:
    """Convertit str → UUID (ou passe-through) ; None si invalide."""
    if v is None:
        return None
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except Exception:
        return None


def _require_uuid(v: str | uuid.UUID | None, *, field: str) -> uuid.UUID:
    """
    Variante stricte : lève si invalide (utile pour les champs NOT NULL en DB).
    """
    u = _coerce_uuid(v)
    if u is None:
        raise ValueError(f"OutboxRepository.insert: invalid {field}={v!r}")
    return u


def _as_utc(dt_val: datetime) -> datetime:
    """
    Normalise un datetime en UTC timezone-aware.
    - si naïf: on suppose UTC
    - sinon: conversion UTC
    """
    if dt_val.tzinfo is None:
        return dt_val.replace(tzinfo=timezone.utc)
    return dt_val.astimezone(timezone.utc)


class OutboxRepository:
    def __init__(self, session: Session):
        self.s = session

    # --- Create ---------------------------------------------------------------

    def insert(
        self,
        type_: str,
        payload: dict,
        client_id: str | uuid.UUID | None,
        incident_id: str | uuid.UUID | None,
        next_attempt_at: datetime | None = None,
    ) -> OutboxEvent:
        """
        Insère un évènement pending planifié à `next_attempt_at` (now UTC par défaut).
        Commit immédiat (pattern simple).
        """

        # client_id est NOT NULL côté DB -> on refuse les valeurs invalides
        cid = _require_uuid(client_id, field="client_id")

        evt = OutboxEvent(
            id=uuid.uuid4(),
            type=type_,
            payload=payload,
            status=OutboxStatus.PENDING,
            attempts=0,
            next_attempt_at=next_attempt_at or datetime.now(timezone.utc),
            client_id=cid,
            incident_id=_coerce_uuid(incident_id),
        )
        self.s.add(evt)
        self.s.commit()
        self.s.refresh(evt)
        return evt

    # --- Read ----------------------------------------------------------------

    def fetch_due(
        self,
        *,
        limit: Optional[int] = None,
        as_of: Optional[datetime] = None,
        now: Optional[datetime] = None,  # rétro-compat si l’appel existant passait `now=...`
        include_status: Sequence[OutboxStatus] = (OutboxStatus.PENDING, OutboxStatus.DELIVERING),
    ) -> list[OutboxEvent]:
        """
        Retourne les évènements à livrer :
        - status ∈ include_status (par défaut: pending, delivering)
        - next_attempt_at <= pivot (pivot = as_of or now or datetime.now(UTC))
        - triés par next_attempt_at asc, limit optionnelle
        """
        pivot = as_of or now or datetime.now(timezone.utc)

        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status.in_(include_status),
                # next_attempt_at peut être NULL (modèle le permet) :
                # on les considère "dus" immédiatement.
                or_(
                    OutboxEvent.next_attempt_at.is_(None),
                    OutboxEvent.next_attempt_at <= pivot,
                ),
            )
            .order_by(OutboxEvent.next_attempt_at.asc())
        )
        if limit:
            stmt = stmt.limit(limit)

        return list(self.s.scalars(stmt))

    # --- Update ---------------------------------------------------------------

    def mark_delivering(self, event_id: str | uuid.UUID) -> int:
        """
        Passe en DELIVERING et incrémente attempts. Retourne la valeur d’`attempts`.
        """
        evt_id = _coerce_uuid(event_id)
        if not evt_id:
            return 0
        evt = self.s.get(OutboxEvent, evt_id)
        if not evt:
            return 0

        evt.status = OutboxStatus.DELIVERING
        evt.attempts = (evt.attempts or 0) + 1
        evt.updated_at = datetime.now(timezone.utc)
        self.s.commit()
        return evt.attempts

    def mark_retry(self, event_id: str | uuid.UUID, when: datetime) -> None:
        """
        Reprogramme en PENDING à 'when' (sans modifier attempts ici).
        """
        evt_id = _coerce_uuid(event_id)
        if not evt_id:
            return
        evt = self.s.get(OutboxEvent, evt_id)
        if not evt:
            return

        evt.status = OutboxStatus.PENDING
        evt.next_attempt_at = _as_utc(when)
        evt.updated_at = datetime.now(timezone.utc)
        self.s.commit()

    def mark_delivered(self, event_id: str | uuid.UUID, receipt: dict | None = None) -> None:
        """
        Marque comme DELIVERED et stocke un reçu éventuel.
        """
        evt_id = _coerce_uuid(event_id)
        if not evt_id:
            return
        evt = self.s.get(OutboxEvent, evt_id)
        if not evt:
            return

        evt.status = OutboxStatus.DELIVERED
        if receipt is not None:
            evt.delivery_receipt = receipt
        evt.updated_at = datetime.now(timezone.utc)
        self.s.commit()

    def mark_failed(self, event_id: str | uuid.UUID, reason: str) -> None:
        """
        Marque comme FAILED avec le dernier message d’erreur.
        """
        evt_id = _coerce_uuid(event_id)
        if not evt_id:
            return
        evt = self.s.get(OutboxEvent, evt_id)
        if not evt:
            return

        evt.status = OutboxStatus.FAILED
        evt.last_error = reason
        evt.updated_at = datetime.now(timezone.utc)
        self.s.commit()
