"""Environment configuration.

Missing required values exit with a short actionable message instead of an
unexplained traceback.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    secret_key: str = Field(min_length=16)
    credential_encryption_key: str = Field(min_length=16)
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    storage_dir: str = "./storage"
    storage_backend: str = "local"
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    cors_origins: str = "http://localhost:3000"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    allow_local_targets: bool = False
    max_upload_bytes: int = 52_428_800
    max_archive_files: int = 5_000
    max_archive_uncompressed_bytes: int = 200_000_000
    log_level: str = "INFO"
    requirements_dir: str = ""
    public_app_url: str = "http://localhost:3000"
    api_public_url: str = "http://localhost:8000"
    access_token_ttl_seconds: int = 60 * 60 * 24 * 7
    require_workers: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def resolved_requirements_dir(self) -> Path:
        if self.requirements_dir:
            return Path(self.requirements_dir)
        return Path(__file__).resolve().parents[4] / "requirements"


def validate_production(settings: Settings) -> None:
    """Refuse unsafe production configuration before serving traffic."""
    if settings.app_env != "production":
        return
    problems: list[str] = []
    if len(settings.secret_key) < 32 or "change" in settings.secret_key.lower():
        problems.append("SECRET_KEY must be a unique value of at least 32 characters.")
    if settings.allow_local_targets:
        problems.append("ALLOW_LOCAL_TARGETS must be false in production.")
    if settings.cookie_samesite == "none" and not settings.cookie_secure:
        problems.append("COOKIE_SECURE must be true when COOKIE_SAMESITE=none.")
    lowered = settings.database_url.lower()
    if "docs:docs@" in lowered or "postgres:postgres@" in lowered:
        problems.append("DATABASE_URL still uses a default database password.")
    if settings.storage_backend == "local" and not settings.storage_dir.startswith("/"):
        problems.append("STORAGE_DIR must be an absolute persistent path in production.")
    if problems:
        print("Refusing to start in production:", file=sys.stderr)
        for item in problems:
            print(f"- {item}", file=sys.stderr)
        raise SystemExit(1)


@lru_cache
def get_settings() -> Settings:
    try:
        settings = Settings()
    except ValidationError as exc:
        missing = []
        for error in exc.errors():
            loc = ".".join(str(part) for part in error.get("loc", []))
            missing.append(f"{loc} ({error.get('msg')})")
        print("Configuration error. Fix these environment variables:", file=sys.stderr)
        for item in missing:
            print(f"- {item}", file=sys.stderr)
        print("Run ./scripts/prepare-env.sh, then start the application again.", file=sys.stderr)
        raise SystemExit(1) from None
    validate_production(settings)
    return settings
