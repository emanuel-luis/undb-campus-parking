"""Request and response models of the operator run routes."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

JobName = Literal["daily", "close_day", "warehouse"]
RunStatus = Literal["queued", "running", "succeeded", "failed"]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobName
    service_date: date | None = Field(
        None, description="A past local date; defaults to yesterday in the service time zone."
    )
    mode: Literal["export", "incremental", "full"] | None = Field(
        None,
        description=(
            "close_day: `export` re-exports the day without closing it. warehouse: "
            "`incremental` (default, `dbt build`) or `full` (`dbt source freshness` and "
            "`dbt build --full-refresh`). daily takes no mode."
        ),
    )


class PipelineRunOut(BaseModel):
    run_id: UUID
    parent_run_id: UUID | None
    job: JobName
    mode: str | None
    trigger: Literal["schedule", "operator", "cli"]
    service_date: date
    status: RunStatus
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    rows_read: int | None
    rows_written: int | None
    rows_rejected: int | None
    metrics: dict[str, Any] | None
    error: str | None
