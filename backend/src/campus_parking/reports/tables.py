"""Core tables over schema `warehouse`, built by dbt (backend/warehouse). Read-only.

A separate MetaData, never Alembic's: dbt owns these tables and their DDL.
"""

from sqlalchemy import Boolean, Column, Date, Integer, MetaData, Numeric, Table, Text

from campus_parking.core.db import UTCDateTime

metadata = MetaData(schema="warehouse")


def _decimal() -> Numeric[float]:
    return Numeric(10, 2, asdecimal=False)


dim_date = Table(
    "dim_date",
    metadata,
    Column("date_key", Integer, primary_key=True),
    Column("date_day", Date),
    Column("iso_day_of_week", Integer),
    Column("weekday_name", Text),
    Column("is_holiday", Boolean),
)

mart_lot_daily = Table(
    "mart_lot_daily",
    metadata,
    Column("lot_id", Text, primary_key=True),
    Column("service_date", Date, primary_key=True),
    Column("date_key", Integer),
    Column("capacity", Integer),
    Column("entries", Integer),
    Column("exits", Integer),
    Column("unique_students", Integer),
    Column("entries_ma7", _decimal()),
    Column("avg_stay_min", _decimal()),
    Column("median_stay_min", _decimal()),
    Column("p90_stay_min", _decimal()),
    Column("peak_occupied", Integer),
    Column("peak_slot", Text),
    Column("min_free", Integer),
    Column("minutes_full", _decimal()),
    Column("stays", Integer),
    Column("turnover", _decimal()),
    Column("closed_by_reconciliation", Integer),
    Column("exit_only", Integer),
    Column("missing_exit", Integer),
)

mart_lot_hourly = Table(
    "mart_lot_hourly",
    metadata,
    Column("lot_id", Text, primary_key=True),
    Column("service_date", Date, primary_key=True),
    Column("hour", Integer, primary_key=True),
    Column("date_key", Integer),
    Column("capacity", Integer),
    Column("entries", Integer),
    Column("exits", Integer),
    Column("avg_free", _decimal()),
    Column("avg_occupied", _decimal()),
    Column("max_occupied", Integer),
)

mart_lot_dwell_daily = Table(
    "mart_lot_dwell_daily",
    metadata,
    Column("lot_id", Text, primary_key=True),
    Column("service_date", Date, primary_key=True),
    Column("duration_bucket", Text, primary_key=True),
    Column("date_key", Integer),
    Column("bucket_order", Integer),
    Column("stays", Integer),
)

REJECT_REASONS = (
    "schema_invalid",
    "unknown_gate",
    "unknown_student",
    "lot_mismatch",
    "direction_mismatch",
    "in_future",
    "too_old",
    "event_id_conflict",
)

mart_data_quality_daily = Table(
    "mart_data_quality_daily",
    metadata,
    Column("service_date", Date, primary_key=True),
    Column("lot_id", Text, primary_key=True),
    Column("date_key", Integer),
    Column("rejects_total", Integer),
    *(Column(f"rejects_{reason}", Integer) for reason in REJECT_REASONS),
    Column("closed_by_reconciliation", Integer),
    Column("exit_only", Integer),
    Column("missing_exit", Integer),
)

fct_occupancy_snapshot = Table(
    "fct_occupancy_snapshot",
    metadata,
    Column("lot_id", Text, primary_key=True),
    Column("service_date", Date, primary_key=True),
    Column("slot_key", Integer, primary_key=True),
    Column("slot_start", UTCDateTime),
    Column("occupied_end", Integer),
    Column("occupied_max", Integer),
    Column("occupied_avg", _decimal()),
    Column("capacity", Integer),
    Column("free_end", Integer),
    Column("free_min", Integer),
    Column("seconds_full", Integer),
)
