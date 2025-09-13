from __future__ import annotations
"""server/app/core/config.py
~~~~~~~~~~~~~~~~~~~~~~~~
Paramètres (pydantic-settings).
"""
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@db:5432/monitoring"
    REDIS_URL: str = "redis://redis:6379/0"
    JWT_SECRET: str = "change-me"
    NO_DATA_MINUTES: int = 5
    KO_CONSECUTIVE_DEFAULT: int = 2
    SMTP_DSN: Optional[str] = None
    ALERT_REMINDER_MINUTES: int = Field(15, env="ALERT_REMINDER_MINUTES")
    SLACK_WEBHOOK: Optional[str] = None
    SLACK_DEFAULT_CHANNEL: str = "#notif-webhook"
    CORS_ALLOW_ORIGINS: Optional[str] = None
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

settings = Settings()
