from __future__ import annotations
"""server/app/core/security.py
~~~~~~~~~~~~~~~~~~~~~~~~
Sécurité API key (header X-API-Key).
"""
from typing import Optional
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.repositories.api_key_repository import ApiKeyRepository

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.repositories.api_key_repository import ApiKeyRepository

async def api_key_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    session: Session = Depends(get_session),
):
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
    repo = ApiKeyRepository(session)
    key = repo.get_by_key(x_api_key)
    if not key or not getattr(key, "is_active", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    return key
