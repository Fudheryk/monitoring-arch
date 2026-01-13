from __future__ import annotations
"""
server/app/api/v1/endpoints/http_targets.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Endpoints minimalistes pour les "HTTP targets" (gestion des URLs à monitorer).
"""

import uuid
from typing import Any, Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import delete, select, update
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert  # type: ignore

from app.presentation.api.deps import api_key_auth
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.http_target import HttpTarget
from app.api.schemas.http_target import HttpTargetIn
from app.api.v1.serializers.http_target import serialize_http_target


router = APIRouter(prefix="/http-targets", tags=["http-targets"])

def _normalize_method(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else str(value)
    return (raw or "").upper()

def normalize_url(raw: str) -> str:
    p = urlparse(str(raw).strip())
    scheme = p.scheme or "http"
    host = p.hostname or p.netloc
    # garde seulement scheme + host, sans path/query/fragment et SANS slash final
    return f"{scheme}://{host}".rstrip("/")

def _is_postgres(db: Session) -> bool:
    try:
        engine = None
        get_bind = getattr(db, "get_bind", None)
        if callable(get_bind):
            engine = get_bind()
        if engine is None:
            engine = getattr(db, "bind", None)
        dialect = getattr(engine, "dialect", None)
        name = getattr(dialect, "name", "")
        return name == "postgresql"
    except Exception:
        return False

@router.get("", response_model=list[dict])
async def list_targets(api_key=Depends(api_key_auth), db: Session = Depends(get_db)) -> list[dict]:
    rows: Iterable[HttpTarget] = db.scalars(
        select(HttpTarget)
        .where(HttpTarget.client_id == api_key.client_id)
        .order_by(HttpTarget.name)
    ).all()
    return [serialize_http_target(t) for t in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_target(
    payload: HttpTargetIn,
    # ✅ idem : dépend toujours du même callable importé depuis deps
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    """
    Création idempotente concurrent-safe via UPSERT PostgreSQL.

    - INSERT ... ON CONFLICT (client_id, url) DO NOTHING RETURNING id
    - Si insert -> 201 (id retourné)
    - Sinon -> 409 + existing_id (idempotence, course gérée)
    """

    # Normaliser la méthode HTTP (Enum/str -> UPPER) pour rester <= VARCHAR(10)
    method_value = (
        payload.method.value if hasattr(payload.method, "value") else str(payload.method)
    ).upper()

    t = HttpTarget.__table__

    parsed = urlparse(str(payload.url))
    normalized_url = normalize_url(payload.url)

    stmt = (
        pg_insert(t)
        .values(
            id=uuid.uuid4(),
            client_id=api_key.client_id,
            name=payload.name,
            url=normalized_url,
            method=method_value,
            timeout_seconds=payload.timeout_seconds,
            check_interval_seconds=payload.check_interval_seconds,
            is_active=payload.is_active,
        )
        .on_conflict_do_nothing(constraint="uq_http_targets_client_url")
        .returning(t.c.id)
    )

    try:
        new_id = db.execute(stmt).scalar_one_or_none()
        db.commit()
    except DataError as e:
        db.rollback()
        # Valeur trop longue / type invalide -> 422 propre
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Invalid value for one of the fields (too long/invalid)."},
        ) from e
    except IntegrityError:
        # Par sécurité : si PG remonte une IntegrityError malgré DO NOTHING
        db.rollback()
        new_id = None

    if new_id:
        return {"id": str(new_id)}

    # Déjà existant (concurrent ou répétition) -> 409 + existing_id
    existing_id = db.scalar(
        select(HttpTarget.id).where(
            (HttpTarget.client_id == api_key.client_id)
            & (HttpTarget.url == str(payload.url))
        )
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "An HTTP target with this URL already exists for this client.",
            "existing_id": str(existing_id) if existing_id else None,
        },
    )

@router.put("/{target_id}")
async def update_target(
    target_id: UUID,
    payload: dict,
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    to_update: dict[str, Any] = {}
    allowed = {
        "name", "url", "method", "accepted_status_codes",
        "timeout_seconds", "check_interval_seconds", "is_active",
    }
    for k in allowed:
        if k in payload:
            to_update[k] = payload[k]
    if "method" in to_update:
        to_update["method"] = _normalize_method(to_update["method"])
    if "url" in to_update and to_update["url"] is not None:
        to_update["url"] = normalize_url(to_update["url"])
        
    try:
        res = db.execute(
            update(HttpTarget)
            .where(HttpTarget.id == target_id, HttpTarget.client_id == api_key.client_id)
            .values(**to_update)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except DataError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Invalid value for one of the fields (too long/invalid)."},
        ) from e
    except IntegrityError:
        db.rollback()
        existing_id = db.scalar(
            select(HttpTarget.id).where(
                (HttpTarget.client_id == api_key.client_id)
                & (HttpTarget.url == to_update.get("url"))
            )
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "An HTTP target with this URL already exists for this client.",
                "existing_id": str(existing_id) if existing_id else None,
            },
        )
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="not found")
    return {"id": str(target_id)}

@router.delete("/{target_id}")
async def delete_target(
    target_id: UUID,
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    res = db.execute(
        delete(HttpTarget).where(
            HttpTarget.id == target_id,
            HttpTarget.client_id == api_key.client_id,
        )
    )
    db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": str(target_id)}
