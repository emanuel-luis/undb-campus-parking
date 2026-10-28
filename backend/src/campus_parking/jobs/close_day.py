"""End-of-day reconciliation: close a past service day, fix the counters, export the day."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus_parking.core.clock import day_cutoff, service_date
from campus_parking.core.problems import DayNotClosed
from campus_parking.core.settings import Settings
from campus_parking.ingestion.models import GateEventRow
from campus_parking.jobs.export import export_day
from campus_parking.jobs.models import ServiceDay
from campus_parking.jobs.runs import RunResult
from campus_parking.occupancy.models import LotOccupancy, Stay
from campus_parking.occupancy.repository import OccupancyRepository
from campus_parking.registry.models import Lot

# An ingest request takes its `received_at` before it waits for a lot lock (at most the lock
# timeout), so an event can be stamped a little before the close that it still lands after.
RECEIVED_SLACK = timedelta(seconds=5)


@dataclass(frozen=True)
class Reconciled:
    closed: int  # open stays set to closed_by_reconciliation by this run
    drift: dict[str, int]  # lot -> (recount - expected) for lots whose counter was wrong
    late_events: int  # events of the day received after the previous close
    events_read: int
    stays_by_status: dict[str, int]


def require_past(day: date, now: datetime, settings: Settings) -> None:
    if day >= service_date(now, settings.tzinfo):
        raise DayNotClosed(f"{day} is not over yet in {settings.timezone}.")


async def reconcile_day(
    session: AsyncSession, settings: Settings, day: date, run_id: UUID | None, now: datetime
) -> Reconciled:
    """One transaction under the lot locks (the order ingest uses): close the day's open stays,
    fix each lot's counter to the recount, record the close. Idempotent."""
    require_past(day, now, settings)
    repo = OccupancyRepository(session)
    lots = sorted(await session.scalars(select(Lot.lot_id)))
    if lots:
        await repo.lock_lots(lots, now)

    previous = await session.scalar(
        select(ServiceDay.closed_at).where(ServiceDay.service_date == day)
    )
    open_on_day = dict(
        (
            await session.execute(
                select(Stay.lot_id, func.count())
                .where(Stay.service_date == day, Stay.status == "open")
                .group_by(Stay.lot_id)
            )
        ).tuples()
    )
    await session.execute(
        update(Stay)
        .where(Stay.service_date == day, Stay.status == "open")
        .values(
            status="closed_by_reconciliation",
            ended_at=day_cutoff(day, settings.tzinfo),
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )

    recount = dict(
        (
            await session.execute(
                select(Stay.lot_id, func.count()).where(Stay.status == "open").group_by(Stay.lot_id)
            )
        ).tuples()
    )
    drift: dict[str, int] = {}
    stored = dict(
        (await session.execute(select(LotOccupancy.lot_id, LotOccupancy.occupied))).tuples()
    )
    for lot_id in sorted(stored):
        closed_here = open_on_day.get(lot_id, 0)
        actual = recount.get(lot_id, 0)
        expected = stored[lot_id] - closed_here
        if actual != expected:
            drift[lot_id] = actual - expected
        if closed_here or actual != stored[lot_id]:
            # The realtime poller watches `version`, so every change bumps it.
            await session.execute(
                update(LotOccupancy)
                .where(LotOccupancy.lot_id == lot_id)
                .values(occupied=actual, version=LotOccupancy.version + 1, updated_at=now)
            )

    events_read = await session.scalar(
        select(func.count()).select_from(GateEventRow).where(GateEventRow.service_date == day)
    )
    late = 0
    if previous is not None:
        late = (
            await session.scalar(
                select(func.count())
                .select_from(GateEventRow)
                .where(GateEventRow.service_date == day, GateEventRow.received_at > previous)
            )
            or 0
        )
    by_status = dict(
        (
            await session.execute(
                select(Stay.status, func.count())
                .where(Stay.service_date == day)
                .group_by(Stay.status)
            )
        ).tuples()
    )
    await session.merge(ServiceDay(service_date=day, closed_at=now, run_id=run_id))
    await session.commit()
    return Reconciled(
        closed=sum(open_on_day.values()),
        drift=drift,
        late_events=late,
        events_read=events_read or 0,
        stays_by_status=by_status,
    )


async def export_into(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    day: date,
    run_id: UUID,
    result: RunResult,
) -> dict[str, Any]:
    """Export the day and record what was exported in the run result."""
    exports = await export_day(sessionmaker, settings.lake_dir, settings.tzinfo, day, run_id)
    by_dataset = {e.dataset: e for e in exports}
    events, rejects = by_dataset["gate_events"], by_dataset["ingest_rejects"]
    result.rows_written = events.rows + rejects.rows
    result.rows_rejected = rejects.rows
    info = {
        "exported_rows": events.rows,
        "export_status": events.status,
        "rejects_status": rejects.status,
        "sha256": events.sha256,
    }
    result.metrics.update(info)
    return info


async def close_day(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    day: date,
    run_id: UUID,
    now: datetime,
    result: RunResult,
) -> None:
    """Reconcile the day, then export it; metrics go into `result`."""
    async with sessionmaker() as session:
        done = await reconcile_day(session, settings, day, run_id, now)
    result.rows_read = done.events_read
    result.metrics.update(
        events_read=done.events_read,
        stays_by_status=done.stays_by_status,
        closed_by_reconciliation=done.closed,
        late_events=done.late_events,
        drift=done.drift,
        changes=done.closed + sum(abs(v) for v in done.drift.values()),
    )
    await export_into(sessionmaker, settings, day, run_id, result)


async def pending_days(session: AsyncSession, settings: Settings, now: datetime) -> list[date]:
    """Dates `daily` must close, oldest first, within the event age limit: yesterday, past dates
    with events that were never closed, and closed dates that got events after their close."""
    today = service_date(now, settings.tzinfo)
    floor = today - timedelta(days=settings.max_event_age_days)
    closed = dict(
        (await session.execute(select(ServiceDay.service_date, ServiceDay.closed_at))).tuples()
    )
    with_events = await session.scalars(
        select(GateEventRow.service_date)
        .where(GateEventRow.service_date >= floor, GateEventRow.service_date < today)
        .distinct()
    )
    pending = {d for d in with_events if d not in closed}
    if today - timedelta(days=1) not in closed:
        pending.add(today - timedelta(days=1))
    inside = {d: at for d, at in closed.items() if d >= floor}
    if inside:
        # received_at is indexed: only events newer than the oldest close are looked at.
        since = min(inside.values()) - RECEIVED_SLACK
        newest = (
            await session.execute(
                select(GateEventRow.service_date, func.max(GateEventRow.received_at))
                .where(GateEventRow.received_at > since, GateEventRow.service_date >= floor)
                .group_by(GateEventRow.service_date)
            )
        ).tuples()
        pending |= {
            d for d, received in newest if d in inside and received > inside[d] - RECEIVED_SLACK
        }
    return sorted(pending)
