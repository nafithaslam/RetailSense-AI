"""
RetailSense AI — Application Settings
======================================
Centralises every configurable value into a single, validated Pydantic Settings
model.  Values are loaded from environment variables (or an optional .env file).

Usage
-----
    from app.core.config import settings

    print(settings.APP_NAME)
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration.

    All attributes map 1-to-1 with environment variables defined in
    .env.example.  Pydantic validates types at startup so misconfigured
    environments are caught immediately.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Application                                                          #
    # ------------------------------------------------------------------ #
    APP_NAME: str = "RetailSense AI"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ------------------------------------------------------------------ #
    # API                                                                  #
    # ------------------------------------------------------------------ #
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    @property
    def cors_origins(self) -> List[str]:
        """Parse ALLOWED_ORIGINS into a list of origin strings."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]

    # ------------------------------------------------------------------ #
    # Database                                                             #
    # ------------------------------------------------------------------ #
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/retailsense"
    )
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30

    # ------------------------------------------------------------------ #
    # Security — reserved for future JWT integration                       #
    # ------------------------------------------------------------------ #
    SECRET_KEY: str = "changeme-use-a-long-random-string-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ------------------------------------------------------------------ #
    # Derived helpers                                                       #
    # ------------------------------------------------------------------ #
    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in valid_levels:
            raise ValueError(
                f"LOG_LEVEL must be one of {valid_levels}, got '{value}'"
            )
        return upper

    @field_validator("APP_ENV")
    @classmethod
    def validate_app_env(cls, value: str) -> str:
        valid_envs = {"development", "staging", "production"}
        if value not in valid_envs:
            raise ValueError(
                f"APP_ENV must be one of {valid_envs}, got '{value}'"
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton pattern)."""
    return Settings()


# Module-level convenience alias
settings: Settings = get_settings()
