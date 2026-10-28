"""What the worker enqueues on its own clock. Due-logic is pure; the tick does the database part."""

import logging
from collections.abc import Collection
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.clock import service_date
from campus_parking.core.settings import Settings
from campus_parking.jobs.models import PipelineRun
from campus_parking.jobs.queue import enqueue

logger = logging.getLogger(__name__)


def daily_due(
    now: datetime, tz: ZoneInfo, run_at: time, enqueued_for: Collection[date]
) -> date | None:
    """Yesterday's date once the local time has passed `run_at` and no `daily` exists for it."""
    local = now.astimezone(tz)
    if local.time() < run_at:
        return None
    yesterday = local.date() - timedelta(days=1)
    return None if yesterday in enqueued_for else yesterday


def warehouse_due(now: datetime, last_requested_at: datetime | None, interval_min: int) -> bool:
    """True when no warehouse run was requested in the last `interval_min` minutes."""
    return last_requested_at is None or now - last_requested_at >= timedelta(minutes=interval_min)


async def enqueue_warehouse_refresh(
    session: AsyncSession, settings: Settings, now: datetime
) -> PipelineRun | None:
    """warehouse/incremental for today every `warehouse_interval_min`; the run it created."""
    last = await session.scalar(
        select(func.max(PipelineRun.requested_at)).where(PipelineRun.job == "warehouse")
    )
    if not warehouse_due(now, last, settings.warehouse_interval_min):
        return None
    today = service_date(now, settings.tzinfo)
    run, created = await enqueue(session, "warehouse", today, "schedule", now, "incremental")
    return run if created else None


async def tick(session: AsyncSession, settings: Settings, now: datetime) -> list[PipelineRun]:
    """Enqueue what is due at `now`; returns the runs it created."""
    created: list[PipelineRun] = []
    local_yesterday = now.astimezone(settings.tzinfo).date() - timedelta(days=1)
    existing = set(
        await session.scalars(
            select(PipelineRun.service_date).where(
                PipelineRun.job == "daily", PipelineRun.service_date == local_yesterday
            )
        )
    )
    due = daily_due(now, settings.tzinfo, settings.daily_run_at, existing)
    if due is not None:
        run, was_created = await enqueue(session, "daily", due, "schedule", now)
        if was_created:
            created.append(run)
            logger.info("daily enqueued", extra={"service_date": due.isoformat()})
    refresh = await enqueue_warehouse_refresh(session, settings, now)
    if refresh is not None:
        created.append(refresh)
    return created
