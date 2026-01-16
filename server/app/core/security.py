from __future__ import annotations
"""
server/app/core/security.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Sécurité (API keys, mots de passe, JWT) + utilitaires cookies.
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
    "ACCESS_COOKIE",
    "REFRESH_COOKIE",
    # API keys
    "api_key_auth",
    # Passwords
    "hash_password",
    "verify_password",
    "get_password_hash",
    "password_context",
    "pwd_ctx",
    # JWT
    "create_access_token",
    "decode_token",
    "decode_verify_token",
    # Cookies
    "cookie_kwargs",
]

# ── Password hashing ─────────────────────────────────────────────────────────
pwd_ctx = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)

# Aliases historiques.
# Si tu veux supprimer TOUTE compat plus tard, tu peux supprimer ces aliases,
# mais ça touche souvent des seeds/outils internes hors scope "serveur".
password_context = pwd_ctx


def get_password_hash(p: str) -> str:
    """Helper historique utilisé dans quelques seeds/outils."""
    return pwd_ctx.hash(p)


def hash_password(p: str) -> str:
    """Hash d'un mot de passe en bcrypt (via passlib)."""
    return pwd_ctx.hash(p)


def verify_password(p: str, h: str) -> bool:
    """Vérifie un mot de passe brut contre un hash passlib."""
    return pwd_ctx.verify(p, h)


# ── API keys ─────────────────────────────────────────────────────────────────
async def api_key_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    session: Session = Depends(get_db),
) -> ApiKey:
    """
    Auth obligatoire :
    - 401 si header absent/vide
    - 403 si clé invalide ou inactive

    Remarque :
    - On strip() toujours la valeur : évite les soucis d'espaces (curl, proxies, etc.).
    """
    k = (x_api_key or "").strip()
    if not k:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    row = session.scalar(
        select(ApiKey).where(
            ApiKey.key == k,
            ApiKey.is_active.is_(True),
        )
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return row


# ── JWT ──────────────────────────────────────────────────────────────────────
def _jwt_secret() -> str:
    # En prod, JWT_SECRET doit être configuré. La valeur par défaut évite de casser
    # un env local mais n'est pas acceptable en production.
    return os.getenv("JWT_SECRET", "change-me-in-env")


def create_access_token(
    payload: dict,
    *,
    expires_seconds: int = 3600,
    secret: str | None = None,
    algorithm: str = "HS256",
) -> str:
    """
    Crée un JWT d'accès HS256.
    - Ajoute iat si absent.
    - exp est toujours posé.
    """
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    data = dict(payload)
    now = datetime.now(timezone.utc)
    data.setdefault("iat", int(now.timestamp()))
    data["exp"] = now + timedelta(seconds=int(expires_seconds))

    return jwt.encode(data, secret or _jwt_secret(), algorithm=algorithm)


def decode_token(
    token: str,
    *,
    secret: str | None = None,
    algorithms: Optional[List[str]] = None,
) -> Optional[dict]:
    """Decode JWT -> dict, retourne None si token invalide."""
    try:
        return jwt.decode(token, secret or _jwt_secret(), algorithms=algorithms or ["HS256"])
    except JWTError:
        return None


def decode_verify_token(token: str, *, expect_typ: str | None = None) -> dict:
    """
    Decode + validations :
    - 401 invalid_token si JWT invalide
    - 401 invalid_token_type si typ inattendu
    """
    claims = decode_token(token)
    if not claims:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")

    if expect_typ and claims.get("typ") != expect_typ:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_type")

    return claims


# ── Cookies ──────────────────────────────────────────────────────────────────
def cookie_kwargs(max_age: int | None = None) -> dict:
    """
    Options communes pour set_cookie() / delete_cookie().

    - Max-Age doit être int (sinon Starlette peut l’ignorer).
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
