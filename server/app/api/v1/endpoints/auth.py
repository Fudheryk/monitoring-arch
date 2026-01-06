# server/app/api/v1/endpoints/auth.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, Request, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.presentation.api.schemas.auth import LoginIn, MeOut
from app.core.security import (
    verify_password,
    ACCESS_COOKIE, REFRESH_COOKIE, cookie_kwargs,
    create_access_token, decode_token,
)
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.user import User
from app.presentation.api.deps import get_current_user
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def make_access_token(user_id: str, client_id: str) -> str:
    # TTL minutes → secondes
    return create_access_token(
        {"sub": user_id, "cid": client_id, "typ": "access"},
        expires_seconds=int(settings.JWT_ACCESS_TTL_MIN) * 60,
    )


def make_refresh_token(user_id: str, client_id: str) -> str:
    # TTL jours → secondes
    return create_access_token(
        {"sub": user_id, "cid": client_id, "typ": "refresh"},
        expires_seconds=int(settings.JWT_REFRESH_TTL_DAYS) * 86400,
    )


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, response: Response, s: Session = Depends(get_db)):
    # SQLAlchemy 2.0
    user = s.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    access = make_access_token(str(user.id), str(user.client_id))
    refresh = make_refresh_token(str(user.id), str(user.client_id))

    # ⚠️ convertir correctement en secondes (int) pour Max-Age
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        **cookie_kwargs(int(int(settings.JWT_ACCESS_TTL_MIN) * 60)),
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        **cookie_kwargs(int(int(settings.JWT_REFRESH_TTL_DAYS) * 86400)),
    )

    return MeOut(
        id=str(user.id),
        email=user.email,
        client_id=str(user.client_id),
        role=str(user.role),
    )


@router.post("/refresh-cookie")
def refresh_cookie(request: Request, response: Response, s: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="missing_refresh")

    claims = decode_token(raw) or {}
    print("[AUTH_API] refresh-cookie pour user:", claims.get("sub"))

    if claims.get("typ") != "refresh":
        raise HTTPException(status_code=401, detail="wrong_token_type")

    user = s.get(User, claims.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail="user_not_found")

    access = make_access_token(str(user.id), str(user.client_id))
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        **cookie_kwargs(int(int(settings.JWT_ACCESS_TTL_MIN) * 60)),
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    # delete_cookie remet un Max-Age négatif (path doit matcher)
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    role = user.role or ("admin_client" if getattr(user, "is_admin", False) else "user")
    return MeOut(id=str(user.id), email=user.email, client_id=str(user.client_id), role=role)
