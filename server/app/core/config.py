from __future__ import annotations
"""server/app/core/config.py
~~~~~~~~~~~~~~~~~~~~~~~~
Paramètres (pydantic-settings).
"""

import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@db:5432/monitoring"
    DB_CONNECT_TIMEOUT: int = Field(5, env="DB_CONNECT_TIMEOUT")
    REDIS_URL: str = "redis://redis:6379/0"
    
    JWT_SECRET: str = "change-me"
    JWT_ALG: str = "HS256"
    JWT_ACCESS_TTL_MIN: int = 15
    JWT_REFRESH_TTL_DAYS: int = 7
    
    COOKIE_DOMAIN: str | None = None
    COOKIE_SECURE: bool = False # True en prod (HTTPS)
    
    NO_DATA_MINUTES: int = 5

    MONITORING_STARTUP_GRACE_SECONDS: int = Field(
        300, env="MONITORING_STARTUP_GRACE_SECONDS"
    )

    SMTP_DSN: Optional[str] = None
    
    ALERT_REMINDER_MINUTES: int = Field(30, env="ALERT_REMINDER_MINUTES")
    SLACK_WEBHOOK: Optional[str] = None
    SLACK_DEFAULT_CHANNEL: str = "#canal"
    CORS_ALLOW_ORIGINS: Optional[str] = None
    STUB_SLACK: bool = False
    INGEST_FUTURE_MAX_SECONDS: int = 120
    INGEST_LATE_MAX_SECONDS: int = 300  # 5 minutes de tolérance
    
    OUTBOX_BATCH_SIZE: int = 100
    OUTBOX_BACKOFFS: list[int] = [30, 60, 120, 300, 600]  # in seconds
    OUTBOX_JITTER_PCT: float = 0.2  # 20%
    DEFAULT_PERCENT_THRESHOLD: float = 90.0

    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = Field(587, env="SMTP_PORT")
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_USE_TLS: bool = Field(True, env="SMTP_USE_TLS")
    SMTP_FROM: Optional[str] = None

    
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

settings = Settings()
