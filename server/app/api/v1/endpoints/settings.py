from __future__ import annotations
"""server/app/api/v1/endpoints/settings.py
~~~~~~~~~~~~~~~~~~~~~~~~
Client settings.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

# Pour être ISO avec http-targets, utilise la même dépendance :
from app.presentation.api.deps import api_key_auth
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.client_settings import ClientSettings

from app.api.schemas.client_settings import ClientSettingsOut, ClientSettingsUpdate
from app.api.v1.serializers.client_settings import serialize_client_settings

router = APIRouter(prefix="/settings", tags=["settings"])


def _get_or_create_settings(db: Session, client_id) -> ClientSettings:
    """
    Récupère les settings pour un client.
    S'ils n'existent pas, les crée avec les defaults du modèle.
    """
    s = db.scalar(
        select(ClientSettings).where(ClientSettings.client_id == client_id)
    )
    if s:
        return s

    # Création "lazy" avec defaults
    s = ClientSettings(client_id=client_id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("", response_model=ClientSettingsOut)
async def get_settings(
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> ClientSettingsOut:
    """
    Retourne la configuration client.
    Si aucune ligne n'existe, on la crée avec les valeurs par défaut.
    """
    s = _get_or_create_settings(db, api_key.client_id)
    return ClientSettingsOut(**serialize_client_settings(s))


@router.put("", response_model=ClientSettingsOut)
async def update_settings(
    payload: ClientSettingsUpdate,
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> ClientSettingsOut:
    """
    Update partiel "idempotent" des settings client.

    - Si aucune ligne n'existe encore → elle est créée avec les defaults,
      puis les champs fournis sont appliqués.
    - Sinon → on applique seulement les champs du payload.
    """
    s = _get_or_create_settings(db, api_key.client_id)

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(s, field, value)

    try:
        db.commit()
        db.refresh(s)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to save client settings",
        )

    return ClientSettingsOut(**serialize_client_settings(s))
