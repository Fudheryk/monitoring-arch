from __future__ import annotations
"""
server/app/api/v1/endpoints/http_targets.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Endpoints minimalistes pour les "HTTP targets".

Objectifs :
- GET /http-targets  : lister les cibles du client courant
- POST /http-targets : créer une cible
  * Gestion robuste des doublons (409) même en cas de POST concurrents
  * Normalisation de la méthode HTTP (Enum -> str -> upper)
  * Conversion des erreurs SQL de type/longueur en 422 lisible (DataError)

Notes :
- La colonne DB "method" est VARCHAR(10). Si on insère accidentellement
  "HTTPMethod.GET" (repr d'un Enum), Postgres lève une erreur
  "value too long for type character varying(10)". On évite ça en
  normalisant la valeur (ex: "GET") avant l'insert.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.http_target import HttpTarget
from app.api.schemas.http_target import HttpTargetIn

router = APIRouter(prefix="/http-targets", tags=["http-targets"])


@router.get("", response_model=list[dict])
async def list_targets(
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(HttpTarget)
        .where(HttpTarget.client_id == api_key.client_id)
        .order_by(HttpTarget.name)
    ).all()

    return [
        {
            "id": str(t.id),
            "name": t.name,
            "url": t.url,
            "method": t.method,
            "expected_status_code": t.expected_status_code,
            "timeout_seconds": t.timeout_seconds,
            "check_interval_seconds": t.check_interval_seconds,
            "is_active": t.is_active,
        }
        for t in rows
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_target(
    payload: HttpTargetIn,
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

    stmt = (
        pg_insert(t)
        .values(
            id=uuid.uuid4(),
            client_id=api_key.client_id,
            name=payload.name,
            url=str(payload.url),
            method=method_value,
            expected_status_code=payload.expected_status_code,
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
