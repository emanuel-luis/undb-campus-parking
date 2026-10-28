"""Application settings, read from PARKING_* environment variables and an optional .env file."""

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_KEY_LENGTH = 32
_KEY_FIELDS = ("ingest_api_key", "admin_api_key", "operator_api_key")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PARKING_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+asyncpg://parking:parking@localhost:5432/parking"
    cors_origins: list[str] = ["http://localhost:5173"]

    ingest_api_key: SecretStr | None = None
    admin_api_key: SecretStr | None = None
    operator_api_key: SecretStr | None = None

    timezone: str = "America/Fortaleza"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    # Ingestion (used from PR 2)
    ingest_max_batch: int = Field(1000, gt=0)
    ingest_max_concurrency: int = Field(4, gt=0)
    ingest_lock_timeout_ms: int = Field(2000, gt=0)
    max_clock_skew_s: int = Field(300, ge=0)
    max_event_age_days: int = Field(120, gt=0)

    # Live availability (PR 4)
    live_poll_interval_s: float = Field(1.0, gt=0)
    live_heartbeat_s: float = Field(15.0, gt=0)
    live_max_connections: int = Field(500, gt=0)

    # Worker and batch (PR 5 and 6)
    lake_dir: Path = Path("data/lake")
    dbt_project_dir: Path = Path("warehouse")
    warehouse_interval_min: int = Field(15, gt=0)
    daily_run_at: time = time(0, 30)
    worker_poll_s: float = Field(2.0, gt=0)
    worker_metrics_port: int = Field(9100, ge=0, lt=65536)  # 0 turns the exporter off
    worker_heartbeat_file: Path = Path("/tmp/worker-heartbeat")

    # Readiness probe: give up on the database check after this many seconds.
    readiness_timeout_s: float = Field(2.0, gt=0)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError(f"unknown time zone: {value!r}") from exc
        return value

    @field_validator("ingest_api_key", "admin_api_key", "operator_api_key")
    @classmethod
    def _key_length(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < MIN_KEY_LENGTH:
            raise ValueError(f"API keys need at least {MIN_KEY_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _environment_rules(self) -> Self:
        if self.env != "dev" and "*" in self.cors_origins:
            raise ValueError("cors_origins may contain '*' only when env is dev")
        if self.env == "prod":
            missing = [name for name in _KEY_FIELDS if getattr(self, name) is None]
            if missing:
                raise ValueError(f"prod requires: {', '.join(missing)}")
            keys = [getattr(self, name).get_secret_value() for name in _KEY_FIELDS]
            if any(key.startswith("change-me") for key in keys):
                raise ValueError("prod API keys must not be the .env.example placeholders")
            if len(set(keys)) != len(keys):
                raise ValueError("prod API keys must all differ")
            if self.database_url.startswith("sqlite"):
                raise ValueError("a SQLite database_url is not allowed in prod")
        return self

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
