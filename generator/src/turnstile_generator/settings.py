"""Generator settings, read from GENERATOR_* environment variables."""

import tempfile
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from turnstile_generator.scenarios import Scenario


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GENERATOR_", extra="ignore")

    api_url: str = "http://localhost:8000"
    ingest_api_key: SecretStr | None = None
    admin_api_key: SecretStr | None = None

    seed: int = 42
    students: int = Field(default=10_000, ge=1, le=1_000_000)
    scenario: Scenario = "normal"
    rate_multiplier: float = Field(default=1.0, gt=0)
    min_rate_per_min: float = Field(default=2.0, ge=0)
    batch_size: int = Field(default=50, ge=1, le=1000)
    backfill_batch_size: int = Field(default=1000, ge=1, le=1000)
    backfill_days: int = Field(default=0, ge=0)
    flush_interval_s: float = Field(default=5.0, gt=0)
    outbox_max: int = Field(default=20_000, ge=1)
    timezone: str = "America/Fortaleza"
    # Synthetic assumption: the semester starts on this date and the week before it is quiet.
    semester_start: date = date(2026, 8, 3)

    fault_duplicate_rate: float = Field(default=0.01, ge=0.0, le=1.0)
    fault_out_of_order_rate: float = Field(default=0.02, ge=0.0, le=1.0)
    fault_missing_exit_rate: float = Field(default=0.01, ge=0.0, le=1.0)
    fault_missing_entry_rate: float = Field(default=0.005, ge=0.0, le=1.0)
    fault_unknown_student_rate: float = Field(default=0.002, ge=0.0, le=1.0)
    fault_unknown_gate_rate: float = Field(default=0.001, ge=0.0, le=1.0)

    heartbeat_file: Path = Path(tempfile.gettempdir()) / "turnstile-gen-heartbeat"
    heartbeat_max_age_s: float = Field(default=120.0, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
