"""Job tables: the run log that doubles as the queue, and the closed service days."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, Index, Integer, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from campus_parking.core.db import Base, UTCDateTime

_ACTIVE = text("status IN ('queued', 'running')")
_QUEUED = text("status = 'queued'")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        CheckConstraint("job IN ('daily', 'close_day', 'warehouse')", name="job_valid"),
        CheckConstraint("trigger IN ('schedule', 'operator', 'cli')", name="trigger_valid"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="status_valid"
        ),
        # One queued or running run per (job, date): the enqueue conflict target.
        Index(
            "uq_pipeline_runs_job_service_date_active",
            "job",
            "service_date",
            unique=True,
            postgresql_where=_ACTIVE,
            sqlite_where=_ACTIVE,
        ),
        Index(
            "ix_pipeline_runs_requested_at_queued",
            "requested_at",
            postgresql_where=_QUEUED,
            sqlite_where=_QUEUED,
        ),
    )

    run_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    parent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("pipeline_runs.run_id"))
    job: Mapped[str] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text)
    service_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rows_read: Mapped[int | None] = mapped_column(Integer)
    rows_written: Mapped[int | None] = mapped_column(Integer)
    rows_rejected: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[Any] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text)


class ServiceDay(Base):
    """A service date that close_day has closed; `closed_at` moves forward on every reprocess."""

    __tablename__ = "service_days"

    service_date: Mapped[date] = mapped_column(Date, primary_key=True)
    closed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("pipeline_runs.run_id"))
    # gate_events rows the last successful export took; NULL until one succeeds.
    exported_events: Mapped[int | None] = mapped_column(Integer)
