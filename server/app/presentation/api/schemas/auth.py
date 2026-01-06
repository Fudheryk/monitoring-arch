from __future__ import annotations
"""
Schemas Pydantic pour les endpoints d'authentification.
"""
from pydantic import BaseModel, EmailStr


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class MeOut(BaseModel):
    id: str
    email: EmailStr
    client_id: str
    role: str
