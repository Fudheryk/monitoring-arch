from __future__ import annotations
"""server/app/core/config.py
~~~~~~~~~~~~~~~~~~~~~~~~
Paramètres (pydantic-settings).
"""

import os
from typing import Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuration de l'application.
    
    Ordre de priorité pour les variables:
    1. Variables d'environnement
    2. Fichier .env (si présent)
    3. Valeurs par défaut (dev)
    """
    
    # =========================================================================
    # DATABASE CONFIGURATION
    # =========================================================================
    
    # Composants individuels (pour construction flexible)
    POSTGRES_USER: str = Field(default="postgres", env="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field(default="postgres", env="DB_PASSWORD")
    POSTGRES_HOST: str = Field(default="db", env="POSTGRES_HOST")
    POSTGRES_PORT: int = Field(default=5432, env="POSTGRES_PORT")
    POSTGRES_DB: str = Field(default="monitoring", env="POSTGRES_DB")
    
    # DATABASE_URL finale (construite ou fournie)
    # Si DATABASE_URL est fournie explicitement, elle sera utilisée
    # Sinon, elle sera construite depuis les composants ci-dessus
    DATABASE_URL: Optional[str] = Field(default=None, env="DATABASE_URL")
    
    DB_CONNECT_TIMEOUT: int = Field(default=5, env="DB_CONNECT_TIMEOUT")
    
    # =========================================================================
    # REDIS CONFIGURATION
    # =========================================================================
    
    # Composants individuels
    REDIS_HOST: str = Field(default="redis", env="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, env="REDIS_PORT")
    REDIS_PASSWORD: Optional[str] = Field(default=None, env="REDIS_PASSWORD")
    REDIS_DB: int = Field(default=0, env="REDIS_DB")
    
    # REDIS_URL finale (construite ou fournie)
    REDIS_URL: Optional[str] = Field(default=None, env="REDIS_URL")
    
    # =========================================================================
    # JWT / SECURITY
    # =========================================================================
    
    JWT_SECRET: str = Field(default="change-me-in-production", env="JWT_SECRET")
    JWT_ALG: str = Field(default="HS256", env="JWT_ALG")
    JWT_ACCESS_TTL_MIN: int = Field(default=15, env="JWT_ACCESS_TTL_MIN")
    JWT_REFRESH_TTL_DAYS: int = Field(default=7, env="JWT_REFRESH_TTL_DAYS")
    
    # =========================================================================
    # COOKIES
    # =========================================================================
    
    COOKIE_DOMAIN: Optional[str] = Field(default=None, env="COOKIE_DOMAIN")
    COOKIE_SECURE: bool = Field(default=False, env="COOKIE_SECURE")  # True en prod (HTTPS)
    
    # =========================================================================
    # MONITORING
    # =========================================================================
    
    NO_DATA_MINUTES: int = Field(default=5, env="NO_DATA_MINUTES")
    MONITORING_STARTUP_GRACE_SECONDS: int = Field(
        default=300, 
        env="MONITORING_STARTUP_GRACE_SECONDS"
    )
    
    # =========================================================================
    # ALERTS
    # =========================================================================
    
    DEFAULT_GRACE_PERIOD_SECONDS: int = Field(default=120, env="DEFAULT_GRACE_PERIOD_SECONDS")
    DEFAULT_ALERT_REMINDER_MINUTES: int = Field(default=30, env="DEFAULT_ALERT_REMINDER_MINUTES")
    DEFAULT_PERCENT_THRESHOLD: float = Field(
        default=90.0, 
        env="DEFAULT_PERCENT_THRESHOLD"
    )
    
    # =========================================================================
    # INTEGRATIONS
    # =========================================================================
    
    # Slack
    SLACK_WEBHOOK: Optional[str] = Field(default=None, env="SLACK_WEBHOOK")
    SLACK_DEFAULT_CHANNEL: str = Field(default="#canal", env="SLACK_DEFAULT_CHANNEL")
    STUB_SLACK: bool = Field(default=False, env="STUB_SLACK")
    
    # SMTP
    SMTP_DSN: Optional[str] = Field(default=None, env="SMTP_DSN")
    SMTP_HOST: Optional[str] = Field(default=None, env="SMTP_HOST")
    SMTP_PORT: int = Field(default=587, env="SMTP_PORT")
    SMTP_USERNAME: Optional[str] = Field(default=None, env="SMTP_USERNAME")
    SMTP_PASSWORD: Optional[str] = Field(default=None, env="SMTP_PASSWORD")
    SMTP_USE_TLS: bool = Field(default=True, env="SMTP_USE_TLS")
    SMTP_FROM: Optional[str] = Field(default=None, env="SMTP_FROM")
    
    # =========================================================================
    # CORS
    # =========================================================================
    
    CORS_ALLOW_ORIGINS: Optional[str] = Field(default=None, env="CORS_ALLOW_ORIGINS")
    
    # =========================================================================
    # INGESTION
    # =========================================================================
    
    INGEST_FUTURE_MAX_SECONDS: int = Field(
        default=120, 
        env="INGEST_FUTURE_MAX_SECONDS"
    )
    INGEST_LATE_MAX_SECONDS: int = Field(
        default=300,  # 5 minutes de tolérance
        env="INGEST_LATE_MAX_SECONDS"
    )
    
    # =========================================================================
    # OUTBOX PATTERN (Event Sourcing)
    # =========================================================================
    
    OUTBOX_BATCH_SIZE: int = Field(default=100, env="OUTBOX_BATCH_SIZE")
    OUTBOX_BACKOFFS: list[int] = Field(
        default=[30, 60, 120, 300, 600],  # in seconds
        env="OUTBOX_BACKOFFS"
    )
    OUTBOX_JITTER_PCT: float = Field(default=0.2, env="OUTBOX_JITTER_PCT")  # 20%
    
    # =========================================================================
    # PYDANTIC CONFIG
    # =========================================================================
    
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=True,
        # Charger automatiquement depuis .env si présent
        env_file=".env",
        extra="ignore",  # Ignorer les variables d'env non définies
    )
    
    # =========================================================================
    # VALIDATORS - Construction dynamique des URLs
    # =========================================================================
    
    @model_validator(mode='after')
    def build_database_url(self) -> 'Settings':
        """
        Construit DATABASE_URL si elle n'est pas fournie explicitement.
        
        Ordre de priorité:
        1. DATABASE_URL fournie explicitement (env var)
        2. Construction depuis POSTGRES_* components
        
        Format: postgresql+psycopg://user:password@host:port/database
        """
        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql+psycopg://"
                f"{self.POSTGRES_USER}:{self.DB_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}"
                f"/{self.POSTGRES_DB}"
            )
        return self
    
    @model_validator(mode='after')
    def build_redis_url(self) -> 'Settings':
        """
        Construit REDIS_URL si elle n'est pas fournie explicitement.
        
        Ordre de priorité:
        1. REDIS_URL fournie explicitement (env var)
        2. Construction depuis REDIS_* components
        
        Format: 
        - Avec password: redis://:password@host:port/db
        - Sans password: redis://host:port/db
        """
        if not self.REDIS_URL:
            if self.REDIS_PASSWORD:
                # Format avec authentification
                self.REDIS_URL = (
                    f"redis://:{self.REDIS_PASSWORD}"
                    f"@{self.REDIS_HOST}:{self.REDIS_PORT}"
                    f"/{self.REDIS_DB}"
                )
            else:
                # Format sans authentification
                self.REDIS_URL = (
                    f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"
                    f"/{self.REDIS_DB}"
                )
        return self
    
    @field_validator('JWT_SECRET')
    @classmethod
    def validate_jwt_secret_in_production(cls, v: str) -> str:
        """
        Warn si JWT_SECRET est la valeur par défaut en production.
        """
        if v == "change-me-in-production" and os.getenv("ENVIRONMENT") == "production":
            import warnings
            warnings.warn(
                "⚠️  JWT_SECRET utilise la valeur par défaut en production! "
                "Définissez une vraie clé secrète via la variable d'environnement JWT_SECRET",
                UserWarning
            )
        return v


# ============================================================================
# Singleton - Instance globale des settings
# ============================================================================

settings = Settings()


# ============================================================================
# Helper pour debug (optionnel)
# ============================================================================

def print_settings_summary():
    """Affiche un résumé des settings (utile pour debug)."""
    print("=" * 80)
    print("SETTINGS SUMMARY")
    print("=" * 80)
    print(f"DATABASE_URL: {settings.DATABASE_URL[:50]}...")  # Masquer le password
    print(f"REDIS_URL: {settings.REDIS_URL[:50] if settings.REDIS_URL else 'None'}...")
    print(f"JWT_SECRET: {'*' * 20} (hidden)")
    print(f"ENVIRONMENT: {os.getenv('ENVIRONMENT', 'development')}")
    print("=" * 80)


if __name__ == "__main__":
    # Pour tester la config
    print_settings_summary()