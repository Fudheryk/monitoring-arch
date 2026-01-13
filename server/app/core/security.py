# server/app/core/security.py

from __future__ import annotations
"""
Sécurité (API keys, mots de passe, JWT) + utilitaires cookies.

- Contexte passlib : pbkdf2_sha256 (stable et portable)
- create_access_token / decode_* : JWT HS256 (secret via env JWT_SECRET)
- cookie_kwargs : options cohérentes et convertit bien Max-Age (int)
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import Depends, Header, HTTPException, status
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.api_key import ApiKey
from app.infrastructure.persistence.database.session import get_db

# ── Cookies (exportés) ────────────────────────────────────────────────────────
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

__all__ = [
    "ACCESS_COOKIE", "REFRESH_COOKIE",
    "api_key_auth", "api_key_auth_optional", "resolve_api_key_from_value",
    "hash_password", "verify_password", "get_password_hash", "password_context",
    "create_access_token", "decode_token", "decode_verify_token",
    "cookie_kwargs", "pwd_ctx",
]

# Contexte de hachage unique pour toute l'app (utilisé aussi par seeds)
pwd_ctx = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)
# Aliases compatibles éventuels (anciens imports)
password_context = pwd_ctx
def get_password_hash(p: str) -> str:  # compat
    return pwd_ctx.hash(p)

# ── API keys ─────────────────────────────────────────────────────────────────
async def api_key_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    session: Session = Depends(get_db),
):
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
    row = session.scalar(select(ApiKey).where(ApiKey.key == x_api_key, ApiKey.is_active.is_(True)))
    if not row:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    return row

async def api_key_auth_optional(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    session: Session = Depends(get_db),
):
    if not x_api_key:
        return None
    row = session.scalar(select(ApiKey).where(ApiKey.key == x_api_key, ApiKey.is_active.is_(True)))
    if not row:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    return row

def resolve_api_key_from_value(key: str, session: Session) -> ApiKey:
    k = (key or "").strip()
    if not k:
        raise HTTPException(status_code=401, detail="Invalid API key")
    row = session.scalar(select(ApiKey).where(ApiKey.key == k, ApiKey.is_active.is_(True)))
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return row

# ── Passwords ────────────────────────────────────────────────────────────────
def hash_password(p: str) -> str:
    return pwd_ctx.hash(p)

def verify_password(p: str, h: str) -> bool:
    return pwd_ctx.verify(p, h)

# ── JWT ──────────────────────────────────────────────────────────────────────
def _jwt_secret() -> str:
    return os.getenv("JWT_SECRET", "change-me-in-env")

def create_access_token(
    payload: dict,
    *,
    expires_seconds: int = 3600,
    secret: str | None = None,
    algorithm: str = "HS256",
) -> str:
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    data = dict(payload)
    now = datetime.now(timezone.utc)
    data.setdefault("iat", int(now.timestamp()))
    data["exp"] = now + timedelta(seconds=int(expires_seconds))
    return jwt.encode(data, secret or _jwt_secret(), algorithm=algorithm)

def decode_token(token: str, *, secret: str | None = None, algorithms: Optional[List[str]] = None) -> Optional[dict]:
    try:
        return jwt.decode(token, secret or _jwt_secret(), algorithms=algorithms or ["HS256"])
    except JWTError:
        return None

def decode_verify_token(token: str, *, expect_typ: str | None = None) -> dict:
    claims = decode_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="invalid_token")
    if expect_typ and claims.get("typ") != expect_typ:
        raise HTTPException(status_code=401, detail="invalid_token_type")
    return claims

# ── Cookies ──────────────────────────────────────────────────────────────────
def cookie_kwargs(max_age: int | None = None) -> dict:
    """
    Options communes pour set_cookie() / delete_cookie().
    - Max-Age doit être int (sinon Starlette l’ignore).
    - Path explicite pour garantir le clear/overwrite.
    - Secure configurable par env (dev = False).
    """
    kw = {
        "httponly": True,
        "samesite": "Strict",
        "secure": os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
        "path": "/",
    }
    if max_age is not None:
        kw["max_age"] = int(max_age)
    return kw
