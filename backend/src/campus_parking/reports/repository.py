"""Queries over the warehouse marts. Portable SQL (Postgres and SQLite): no percentile
functions; medians and p90 come precomputed per lot and day from the marts."""

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.problems import WarehouseNotReady
from campus_parking.jobs.models import PipelineRun
from campus_parking.registry.models import Lot
from campus_parking.reports.tables import (
    dim_date,
    fct_occupancy_snapshot,
    mart_data_quality_daily,
    mart_lot_daily,
    mart_lot_dwell_daily,
    mart_lot_hourly,
)

Row = dict[str, Any]


async def latest_success(session: AsyncSession) -> PipelineRun | None:
    return await session.scalar(
        select(PipelineRun)
        .where(PipelineRun.job == "warehouse", PipelineRun.status == "succeeded")
        .order_by(PipelineRun.finished_at.desc(), PipelineRun.run_id.desc())
        .limit(1)
    )


async def latest_status(session: AsyncSession) -> str | None:
    return await session.scalar(
        select(PipelineRun.status)
        .where(PipelineRun.job == "warehouse")
        .order_by(PipelineRun.requested_at.desc(), PipelineRun.run_id.desc())
        .limit(1)
    )


async def lot_exists(session: AsyncSession, lot_id: str) -> bool:
    return await session.scalar(select(Lot.lot_id).where(Lot.lot_id == lot_id)) is not None


# undefined_table and invalid_schema_name; any other error (a missing column, say) is a 500.
MISSING_RELATION = {"42P01", "3F000"}


def missing_table(exc: DBAPIError) -> bool:
    """A mart or the warehouse schema does not exist (Postgres SQLSTATE, SQLite message)."""
    orig = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code in MISSING_RELATION:
            return True
    return "no such table" in str(orig)


async def _rows(session: AsyncSession, stmt: Select[Any]) -> Sequence[Row]:
    try:
        result = await session.execute(stmt)
    except DBAPIError as exc:
        if missing_table(exc):
            raise WarehouseNotReady("The warehouse marts have not been built yet.") from exc
        raise
    return [dict(r) for r in result.mappings()]


def _lot(stmt: Select[Any], column: Any, lot_id: str | None) -> Select[Any]:
    return stmt if lot_id is None else stmt.where(column == lot_id)


async def daily(session: AsyncSession, start: date, end: date, lot_id: str | None) -> Sequence[Row]:
    t = mart_lot_daily
    stmt = select(t).where(t.c.service_date.between(start, end))
    return await _rows(
        session, _lot(stmt, t.c.lot_id, lot_id).order_by(t.c.lot_id, t.c.service_date)
    )


async def summary(
    session: AsyncSession, start: date, end: date, lot_id: str | None
) -> Sequence[Row]:
    t = mart_lot_daily
    stmt = (
        select(
            t.c.lot_id,
            func.count().label("days"),
            func.coalesce(func.sum(t.c.entries), 0).label("entries"),
            func.coalesce(func.sum(t.c.exits), 0).label("exits"),
            func.avg(t.c.entries).label("avg_daily_entries"),
            func.avg(t.c.unique_students).label("avg_daily_unique_students"),
            func.max(t.c.peak_occupied).label("peak_occupied"),
            func.min(t.c.min_free).label("min_free"),
            func.coalesce(func.sum(t.c.minutes_full), 0).label("minutes_full"),
            func.avg(t.c.avg_stay_min).label("avg_stay_min"),
            func.avg(t.c.p90_stay_min).label("p90_stay_min"),
            func.avg(t.c.turnover).label("avg_turnover"),
            func.sum(t.c.closed_by_reconciliation).label("closed_by_reconciliation"),
            func.sum(t.c.exit_only).label("exit_only"),
            func.sum(t.c.missing_exit).label("missing_exit"),
        )
        .where(t.c.service_date.between(start, end))
        .group_by(t.c.lot_id)
        .order_by(t.c.lot_id)
    )
    return await _rows(session, _lot(stmt, t.c.lot_id, lot_id))


async def hourly(
    session: AsyncSession, start: date, end: date, lot_id: str | None
) -> Sequence[Row]:
    t = mart_lot_hourly
    stmt = select(t).where(t.c.service_date.between(start, end))
    order = (t.c.lot_id, t.c.service_date, t.c.hour)
    return await _rows(session, _lot(stmt, t.c.lot_id, lot_id).order_by(*order))


async def weekday(
    session: AsyncSession, start: date, end: date, lot_id: str | None
) -> Sequence[Row]:
    t, d = mart_lot_daily, dim_date
    stmt = (
        select(
            t.c.lot_id,
            d.c.iso_day_of_week,
            d.c.weekday_name,
            func.count().label("days"),
            func.avg(t.c.entries).label("avg_entries"),
            func.avg(t.c.exits).label("avg_exits"),
            func.avg(t.c.unique_students).label("avg_unique_students"),
            func.avg(t.c.peak_occupied).label("avg_peak_occupied"),
        )
        .join(d, d.c.date_key == t.c.date_key)
        .where(t.c.service_date.between(start, end), d.c.is_holiday.is_(False))
        .group_by(t.c.lot_id, d.c.iso_day_of_week, d.c.weekday_name)
        .order_by(t.c.lot_id, d.c.iso_day_of_week)
    )
    return await _rows(session, _lot(stmt, t.c.lot_id, lot_id))


async def occupancy(session: AsyncSession, day: date, lot_id: str | None) -> Sequence[Row]:
    t = fct_occupancy_snapshot
    stmt = select(t).where(t.c.service_date == day)
    return await _rows(session, _lot(stmt, t.c.lot_id, lot_id).order_by(t.c.lot_id, t.c.slot_key))


async def dwell(session: AsyncSession, start: date, end: date, lot_id: str | None) -> Sequence[Row]:
    t = mart_lot_dwell_daily
    stmt = (
        select(
            t.c.lot_id,
            t.c.duration_bucket,
            t.c.bucket_order,
            func.coalesce(func.sum(t.c.stays), 0).label("stays"),
        )
        .where(t.c.service_date.between(start, end))
        .group_by(t.c.lot_id, t.c.duration_bucket, t.c.bucket_order)
        .order_by(t.c.lot_id, t.c.bucket_order)
    )
    return await _rows(session, _lot(stmt, t.c.lot_id, lot_id))


async def quality(
    session: AsyncSession, start: date, end: date, lot_id: str | None
) -> Sequence[Row]:
    t = mart_data_quality_daily
    stmt = select(t).where(t.c.service_date.between(start, end))
    return await _rows(
        session, _lot(stmt, t.c.lot_id, lot_id).order_by(t.c.service_date, t.c.lot_id)
    )
