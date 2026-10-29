"""Response models of the report routes (they become the TypeScript client types)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from campus_parking.jobs.schemas import RunStatus

DurationBucket = Literal["<30m", "30-60m", "1-2h", "2-4h", "4-6h", ">6h"]


class ReportFreshness(BaseModel):
    """How old the warehouse data is. Every field is null before the first successful run."""

    last_run_id: UUID | None = Field(description="Latest successful warehouse run.")
    finished_at: datetime | None = Field(description="When that run finished.")
    mode: Literal["incremental", "full"] | None = Field(description="Mode of that run.")
    status: RunStatus | None = Field(
        description="Status of the most recent warehouse run of any outcome (queued, running, "
        "succeeded, failed): a failed refresh shows here while the data stays as of finished_at."
    )
    age_s: float | None = Field(description="Seconds since finished_at.")


class LotReportSummary(BaseModel):
    """One lot over the requested range. Range-level stay figures are means of the daily
    figures, never percentiles of pooled stays."""

    lot_id: str
    from_date: date
    to_date: date
    days: int = Field(description="Days with data in the range.")
    entries: int
    exits: int
    avg_daily_entries: float
    avg_daily_unique_students: float
    peak_occupied: int | None
    min_free: int | None
    minutes_full: float
    avg_stay_min: float | None = Field(description="Mean of the daily average stay (minutes).")
    p90_stay_min: float | None = Field(
        description="Mean of the daily p90 stay (minutes); not a p90 of the whole range."
    )
    avg_turnover: float | None
    closed_by_reconciliation: int
    exit_only: int
    missing_exit: int


class LotDailyRow(BaseModel):
    lot_id: str
    service_date: date
    capacity: int | None
    entries: int
    exits: int
    unique_students: int
    entries_ma7: float | None = Field(description="Mean entries of the day and the 6 before.")
    avg_stay_min: float | None
    median_stay_min: float | None
    p90_stay_min: float | None
    peak_occupied: int | None
    peak_slot: str | None = Field(description="HH:MM start of the first slot at the peak.")
    min_free: int | None
    minutes_full: float | None
    stays: int
    turnover: float | None = Field(description="Stays with an entry per space.")
    closed_by_reconciliation: int
    exit_only: int
    missing_exit: int


class LotHourlyRow(BaseModel):
    lot_id: str
    service_date: date
    hour: int = Field(ge=0, le=23)
    capacity: int | None
    entries: int
    exits: int
    avg_free: float | None
    avg_occupied: float | None
    max_occupied: int | None


class WeekdayRow(BaseModel):
    """Daily means per lot and weekday over the range, holidays excluded."""

    lot_id: str
    iso_day_of_week: int = Field(ge=1, le=7, description="1 Monday to 7 Sunday.")
    weekday_name: str = Field(description="Portuguese weekday name.")
    days: int
    avg_entries: float
    avg_exits: float
    avg_unique_students: float
    avg_peak_occupied: float | None


class OccupancySlot(BaseModel):
    lot_id: str
    service_date: date
    slot_key: int = Field(ge=0, le=95, description="0 is 00:00-00:15 local.")
    slot_start: datetime
    occupied_end: int
    occupied_max: int
    occupied_avg: float
    capacity: int
    free_end: int
    free_min: int
    seconds_full: int


class DwellBucketRow(BaseModel):
    """Closed stays per duration bucket over the range."""

    lot_id: str
    duration_bucket: DurationBucket
    bucket_order: int
    stays: int


class QualityDailyRow(BaseModel):
    """lot_id '-1' collects rejects without a known lot."""

    service_date: date
    lot_id: str
    rejects_total: int
    rejects_schema_invalid: int
    rejects_unknown_gate: int
    rejects_unknown_student: int
    rejects_lot_mismatch: int
    rejects_direction_mismatch: int
    rejects_in_future: int
    rejects_too_old: int
    rejects_event_id_conflict: int
    closed_by_reconciliation: int
    exit_only: int
    missing_exit: int
