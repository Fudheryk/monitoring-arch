# webapp/app/config.py
from __future__ import annotations

"""
Configuration WebApp.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Configuration WebApp (surchargée par variables d'env et .env).

    Variables utiles :
      - API_BASE_URL    : URL interne du backend API (ex: http://api:8000)
      - LOGIN_PATH      : chemin de la page de login Web (ex: /login)
      - PUBLIC_PATHS    : CSV des chemins publics à laisser passer (HEAD/GET/POST) côté Web
                          ex: "/login,/logout,/static,/_health,/health"
      - ACCESS_COOKIE   : nom du cookie d'accès (aligné avec l'API)
      - REFRESH_COOKIE  : nom du cookie de refresh (aligné avec l'API)
    """
    API_BASE_URL: str = "http://api:8000"
    LOGIN_PATH: str = "/login"
    PUBLIC_PATHS: str = "/login,/logout,/static,/_health,/health"

    # ✅ noms de cookies alignés avec l'API (app.core.security)
    ACCESS_COOKIE: str = "access_token"
    REFRESH_COOKIE: str = "refresh_token"

    class Config:
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()
