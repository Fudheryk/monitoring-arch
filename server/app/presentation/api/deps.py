from __future__ import annotations
"""
server/app/presentation/api/deps.py
~~~~~~~~~~~~~~~~~~~~~~~~
Dépendances communes côté présentation (API).

- get_current_user : JWT via cookie d’accès.
- Re-export des deps d’API key pour compat tests : `api_key_auth`, `api_key_auth_optional`.
"""

from fastapi import Depends, Cookie, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import ACCESS_COOKIE, decode_verify_token
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.user import User

# ⬇️ Re-export : **même objet callable** que dans app.core.security
#    → Les tests qui overrident deps.api_key_auth OU security.api_key_auth fonctionneront.
from app.core.security import api_key_auth, api_key_auth_optional  # noqa: F401


def get_current_user(
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    s: Session = Depends(get_db),
) -> User:
    """
    Récupère l'utilisateur courant à partir du cookie JWT d'accès.
    - 401 si cookie manquant / token invalide / user introuvable
    - Vérifie aussi que le token est bien de type "access"
    """
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_token")

    claims = decode_verify_token(access_token, expect_typ="access")

    user = s.get(User, claims.get("sub"))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_not_found")

    return user
