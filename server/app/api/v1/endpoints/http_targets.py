from __future__ import annotations
"""
server/app/api/v1/endpoints/http_targets.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Endpoints "HTTP targets" (gestion des URLs à monitorer) — JWT cookies.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert  # type: ignore
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas.http_target import HttpTargetIn
from app.api.v1.serializers.http_target import serialize_http_target
from app.infrastructure.persistence.database.models.http_target import HttpTarget
from app.infrastructure.persistence.database.session import get_db
from app.presentation.api.deps import get_current_user

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


@router.get("", response_model=list[dict])
async def list_targets(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """
    Liste des targets pour le tenant courant.
    """
    client_id = getattr(current_user, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_client_id")

    rows: Iterable[HttpTarget] = db.scalars(
        select(HttpTarget)
        .where(HttpTarget.client_id == client_id)
        .order_by(HttpTarget.name)
    ).all()
    return [serialize_http_target(t) for t in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_target(
    payload: HttpTargetIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Création idempotente concurrent-safe via UPSERT PostgreSQL.

    - INSERT ... ON CONFLICT (client_id, url) DO NOTHING RETURNING id
    - Si insert -> 201 (id retourné)
    - Sinon -> 409 + existing_id (idempotence, course gérée)
    """
    client_id = getattr(current_user, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_client_id")

    # Normaliser la méthode HTTP (Enum/str -> UPPER) pour rester <= VARCHAR(10)
    method_value = _normalize_method(payload.method)

    normalized_url = normalize_url(payload.url)

    t = HttpTarget.__table__

    # ⚠️ UPSERT PostgreSQL : nécessite un constraint name valide.
    stmt = (
        pg_insert(t)
        .values(
            id=uuid.uuid4(),
            client_id=client_id,
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
    # ⚠️ IMPORTANT: on doit requêter avec normalized_url (pas payload.url brut),
    # sinon l'ID peut ne pas être trouvé si l'URL a été normalisée.
    existing_id = db.scalar(
        select(HttpTarget.id).where(
            (HttpTarget.client_id == client_id)
            & (HttpTarget.url == normalized_url)
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
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Mise à jour d'un target (tenant-scoped).
    On autorise un sous-ensemble de champs (whitelist).
    """
    client_id = getattr(current_user, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_client_id")

    to_update: dict[str, Any] = {}
    allowed = {
        "name",
        "url",
        "method",
        "accepted_status_codes",
        "timeout_seconds",
        "check_interval_seconds",
        "is_active",
    }
    for k in allowed:
        if k in payload:
            to_update[k] = payload[k]

    # Normalisations (défensives)
    if "method" in to_update:
        to_update["method"] = _normalize_method(to_update["method"])
    if "url" in to_update and to_update["url"] is not None:
        to_update["url"] = normalize_url(to_update["url"])

    try:
        res = db.execute(
            update(HttpTarget)
            .where(HttpTarget.id == target_id, HttpTarget.client_id == client_id)
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
        # Si le changement d'URL viole l'unicité (client_id,url)
        existing_id = None
        if to_update.get("url"):
            existing_id = db.scalar(
                select(HttpTarget.id).where(
                    (HttpTarget.client_id == client_id)
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
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Suppression d'un target (tenant-scoped).
    """
    client_id = getattr(current_user, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_client_id")

    res = db.execute(
        delete(HttpTarget).where(
            HttpTarget.id == target_id,
            HttpTarget.client_id == client_id,
        )
    )
    db.commit()

    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="not found")

    return {"deleted": str(target_id)}
