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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, DataError  # DataError -> 422 propre
from sqlalchemy.orm import Session

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.database.models.http_target import HttpTarget
from app.api.schemas.http_target import HttpTargetIn

router = APIRouter(prefix="/http-targets", tags=["http-targets"])


@router.get("", response_model=list[dict])
async def list_targets(
    api_key=Depends(api_key_auth),
    session: Session = Depends(get_session),
) -> list[dict]:
    """
    Retourne la liste des cibles HTTP pour le client authentifié.
    (Réponse simplifiée en dicts pour rester cohérent avec le reste du projet.)
    """
    rows = session.scalars(
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
    session: Session = Depends(get_session),
) -> dict:
    """
    Crée une cible HTTP pour le client courant.

    Stratégie anti-doublon robuste :
    1) Pré-vérification (SELECT) pour court-circuiter un 409 évident (meilleur DX).
       ⚠️ Non suffisante contre la course.
    2) INSERT + COMMIT. Si une contrainte d’unicité en DB (client_id, url) saute,
       on rollback puis on re-vérifie et renvoie un 409 lisible (idempotence).

    Défense en profondeur :
    - Normalisation de la méthode HTTP (Enum/str -> UPPER) pour éviter "HTTPMethod.GET".
    - Conversion des DataError SQL (ex: dépassement VARCHAR) en 422 utilisateur.
    """

    # -------- 1) Pré-vérification optimiste (meilleur DX) --------
    existing_id = session.scalar(
        select(HttpTarget.id).where(
            (HttpTarget.client_id == api_key.client_id)
            & (HttpTarget.url == str(payload.url))
        )
    )
    if existing_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "An HTTP target with this URL already exists for this client.",
                "existing_id": str(existing_id),
            },
        )

    # -------- Normalisation de la méthode HTTP --------
    # Si payload.method est un Enum (ex: HTTPMethod.GET), .value donne "GET".
    # Sinon on cast en str puis on met en MAJ pour cohérence et contraintes éventuelles.
    method_value = (
        payload.method.value  # Enum -> "GET"
        if hasattr(payload.method, "value")
        else str(payload.method)
    ).upper()

    # -------- 2) Insertion --------
    t = HttpTarget(
        client_id=api_key.client_id,
        name=payload.name,
        url=str(payload.url),
        method=method_value,  # <- Corrige l'ancienne insertion de "HTTPMethod.GET"
        expected_status_code=payload.expected_status_code,
        timeout_seconds=payload.timeout_seconds,
        check_interval_seconds=payload.check_interval_seconds,
        is_active=payload.is_active,
    )
    session.add(t)

    try:
        session.commit()

    except IntegrityError:
        # -------- Rattrapage de course (doublon concurrent) --------
        session.rollback()
        existing_id = session.scalar(
            select(HttpTarget.id).where(
                (HttpTarget.client_id == api_key.client_id)
                & (HttpTarget.url == str(payload.url))
            )
        )
        if existing_id:
            # On renvoie un 409 lisible et idempotent avec l'id existant.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "An HTTP target with this URL already exists for this client.",
                    "existing_id": str(existing_id),
                },
            )
        # Ce n'était pas un doublon : on propage pour journalisation globale (500).
        raise

    except DataError as e:
        # -------- Défense en profondeur (ex: VARCHAR trop court/long) --------
        # Exemple typique : "value too long for type character varying(10)" si
        # on insère une méthode invalide. On retourne un 422 lisible plutôt qu'un 500.
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Invalid value for one of the fields (too long/invalid).",
                # Astuce: en dev, on peut décommenter la clé suivante pour diagnostiquer.
                # "db_error": str(e),
            },
        ) from e

    # -------- Succès --------
    session.refresh(t)
    return {"id": str(t.id)}
