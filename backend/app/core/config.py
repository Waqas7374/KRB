"""Application configuration.

One `Settings` object, populated from the environment and validated at import
time. A missing or malformed secret fails the process at startup rather than at
3 a.m. on first use.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---------------------------------------------------------
    app_name: str = "KRB ERP"
    app_env: Environment = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    root_path: str = ""

    # --- Logging -------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    # --- Security ------------------------------------------------------------
    secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)
    password_reset_ttl_minutes: int = Field(default=30, ge=5, le=1440)
    invite_link_ttl_hours: int = Field(default=72, ge=1, le=336)
    max_failed_logins: int = Field(default=5, ge=1)
    lockout_minutes: int = Field(default=15, ge=1)
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost: int = Field(default=65536, ge=8192)
    argon2_parallelism: int = Field(default=4, ge=1)

    # --- CORS ----------------------------------------------------------------
    # NoDecode: pydantic-settings JSON-decodes complex types before field
    # validators run; without it a plain comma-separated value is an error.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cors_allow_credentials: bool = True

    # --- Database ------------------------------------------------------------
    database_url: Annotated[str, Field(min_length=1)]
    database_url_sync: Annotated[str, Field(min_length=1)]
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60)
    db_echo: bool = False

    # --- Redis / Celery ------------------------------------------------------
    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")  # type: ignore[assignment]
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- Object storage ------------------------------------------------------
    storage_endpoint_url: str | None = None
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_bucket: str = "krb-erp"
    storage_region: str = "auto"
    storage_public_base_url: str | None = None
    storage_presign_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    allowed_upload_types: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "image/jpeg",
            "image/png",
            "image/webp",
            "application/pdf",
        ]
    )

    # --- Email ---------------------------------------------------------------
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = False
    mail_from: str = "no-reply@krb-erp.local"
    mail_from_name: str = "KRB ERP"
    # Where emailed links (invitations, password resets) point. Must be the
    # browser-facing origin of the web app, not the API.
    web_base_url: str = "http://localhost:5173"

    # --- Rate limiting -------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = 600
    rate_limit_login_per_15min: int = 5
    rate_limit_sync_per_minute: int = 60

    # --- Company defaults (Pakistan — docs/12-confirmed-decisions.md) --------
    default_company_code: str = "KRB"
    default_currency: str = Field(default="PKR", min_length=3, max_length=3)
    default_timezone: str = "Asia/Karachi"
    default_locale: str = "en-PK"
    fiscal_year_start_month: int = Field(default=7, ge=1, le=12)

    # --- Observability -------------------------------------------------------
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    metrics_enabled: bool = True

    # ------------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------------
    @field_validator("cors_origins", "allowed_upload_types", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept a comma-separated string as well as a JSON list."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if "+asyncpg" not in value:
            raise ValueError(
                "DATABASE_URL must use the asyncpg driver "
                "(postgresql+asyncpg://...). The sync URL belongs in DATABASE_URL_SYNC."
            )
        PostgresDsn(value.replace("+asyncpg", ""))
        return value

    @field_validator("database_url_sync")
    @classmethod
    def _require_sync_driver(cls, value: str) -> str:
        if "+asyncpg" in value:
            raise ValueError("DATABASE_URL_SYNC must not use asyncpg; Alembic needs a sync driver.")
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        """Refuse to start in production with development-grade settings."""
        if self.app_env != "production":
            return self

        problems: list[str] = []
        if self.debug:
            problems.append("DEBUG must be false in production")
        if "CHANGE_ME" in self.secret_key or len(self.secret_key) < 48:
            problems.append("SECRET_KEY must be a strong, non-placeholder value in production")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must not contain a wildcard in production")
        if not self.cors_origins:
            problems.append("CORS_ORIGINS must be set in production")
        if self.log_format != "json":
            problems.append("LOG_FORMAT should be json in production")
        if not self.storage_access_key or not self.storage_secret_key:
            problems.append("Object storage credentials are required in production")

        if problems:
            raise ValueError("Refusing to start in production:\n  - " + "\n  - ".join(problems))
        return self

    # ------------------------------------------------------------------------
    # Derived
    # ------------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_testing(self) -> bool:
        return self.app_env == "test"

    @property
    def docs_url(self) -> str | None:
        return None if self.is_production else "/docs"

    @property
    def redoc_url(self) -> str | None:
        return None if self.is_production else "/redoc"

    @property
    def openapi_url(self) -> str | None:
        return None if self.is_production else f"{self.api_v1_prefix}/openapi.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Used as a FastAPI dependency and at import time."""
    return Settings()


def generate_secret_key() -> str:
    """Helper for `python -c "from app.core.config import generate_secret_key; print(...)"`."""
    return secrets.token_urlsafe(64)


settings = get_settings()
