from __future__ import annotations
"""
server/app/presentation/api/deps.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Dépendances communes côté "presentation" (API).

Objectif (JWT-only) :
- Centraliser l'authentification "utilisateur" via cookies JWT.
- Supprimer toute compatibilité / re-export de dépendances API key dans cette couche.
  (Les API keys restent réservées aux endpoints d'ingestion/agents, côté app.core.security
   ou une couche "ingest", mais pas ici.)

Contenu :
- get_current_user : lit le cookie d'accès, vérifie le JWT, charge l'utilisateur en DB.
- Helpers optionnels internes pour extraire client_id de manière sûre.

Conventions :
- 401 si cookie manquant / token invalide / user introuvable
- 403 si user inactif (si le modèle User expose un champ d'activation)
"""

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import ACCESS_COOKIE, decode_verify_token
from app.infrastructure.persistence.database.models.user import User
from app.infrastructure.persistence.database.session import get_db


def get_current_user(
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    db: Session = Depends(get_db),
) -> User:
    """
    Récupère l'utilisateur courant à partir du cookie JWT d'accès.

    Sources d'identité :
    - Cookie HttpOnly nommé ACCESS_COOKIE (aligné avec l'API auth)

    Étapes :
    1) Vérifie présence cookie
    2) Décode + vérifie signature/exp/type (expect_typ="access")
    3) Charge l'utilisateur (sub) en base
    4) Optionnel : vérifie l'état actif de l'utilisateur si disponible

    Raises:
        HTTPException(401): cookie manquant, token invalide/expiré, utilisateur introuvable
        HTTPException(403): utilisateur désactivé (si champ présent)
    """
    if not access_token:
        # Ne pas divulguer d'informations. La webapp saura refresh puis rediriger login.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_token",
        )

    # decode_verify_token doit lever une exception (ou HTTPException) si invalide.
    # On s'aligne sur son comportement pour ne pas masquer les raisons internes.
    claims = decode_verify_token(access_token, expect_typ="access")

    sub = claims.get("sub")
    if not sub:
        # Token mal formé
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
        )

    user: User | None = db.get(User, sub)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user_not_found",
        )

    # Défense optionnelle: si ton modèle User a un flag d'activation.
    # On évite de casser si le champ n'existe pas.
    is_active = getattr(user, "is_active", None)
    if is_active is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_inactive",
        )

    return user


def require_client_id(current_user: User = Depends(get_current_user)):
    """
    Dépendance utilitaire : récupère le client_id de l'utilisateur courant.

    Pourquoi:
    - Les endpoints multi-tenant doivent filtrer par client_id.
    - Centralise le contrôle et l'erreur si le user n'est pas correctement rattaché.

    Returns:
        client_id (type dépendant du modèle, souvent uuid.UUID)

    Raises:
        HTTPException(401): si le user ne porte pas de client_id
    """
    client_id = getattr(current_user, "client_id", None)
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_client_id",
        )
    return client_id
