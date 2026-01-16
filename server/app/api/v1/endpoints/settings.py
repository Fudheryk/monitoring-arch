from __future__ import annotations
"""
server/app/api/v1/endpoints/settings.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Client settings (JWT).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.client_settings import ClientSettings

from app.api.schemas.client_settings import ClientSettingsOut, ClientSettingsUpdate
from app.api.v1.serializers.client_settings import serialize_client_settings

# ✅ JWT-only
from app.presentation.api.deps import get_current_user
from app.infrastructure.persistence.database.models.user import User

router = APIRouter(prefix="/settings", tags=["settings"])


def _get_or_create_settings(db: Session, client_id) -> ClientSettings:
    """
    Récupère les settings pour un client.
    S'ils n'existent pas, les crée (idempotent, safe concurrence).

    Gestion concurrence :
    - Deux requêtes simultanées peuvent tenter INSERT.
    - La seconde reçoit IntegrityError -> rollback -> re-select.
    """
    s = db.scalar(select(ClientSettings).where(ClientSettings.client_id == client_id))
    if s:
        return s

    s = ClientSettings(client_id=client_id)
    db.add(s)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        s = db.scalar(select(ClientSettings).where(ClientSettings.client_id == client_id))
        if s:
            return s
        # Si on n'arrive toujours pas à relire, on remonte l'erreur.
        raise exc

    db.refresh(s)
    return s


@router.get("", response_model=ClientSettingsOut)
async def get_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClientSettingsOut:
    """
    Retourne la configuration client.
    Si aucune ligne n'existe, on la crée avec les valeurs par défaut.
    """
    client_id = getattr(current_user, "client_id", None)
    s = _get_or_create_settings(db, client_id)
    return ClientSettingsOut(**serialize_client_settings(s))


@router.put("", response_model=ClientSettingsOut)
async def update_settings(
    payload: ClientSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClientSettingsOut:
    """
    Update partiel des settings client.

    - Si aucune ligne n'existe encore → création lazy, puis application des champs.
    - Sinon → application uniquement des champs présents (exclude_unset=True).
    """
    client_id = getattr(current_user, "client_id", None)
    s = _get_or_create_settings(db, client_id)

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(s, field, value)

    try:
        db.commit()
        db.refresh(s)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to save client settings",
        ) from exc

    return ClientSettingsOut(**serialize_client_settings(s))
