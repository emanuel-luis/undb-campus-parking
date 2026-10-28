"""Request and response models of the operator run routes."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

JobName = Literal["daily", "close_day", "warehouse"]
RunStatus = Literal["queued", "running", "succeeded", "failed"]


class RunRequest(BaseModel):
    job: Literal["daily", "close_day"]
    service_date: date | None = Field(
        None, description="A past local date; defaults to yesterday in the service time zone."
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
