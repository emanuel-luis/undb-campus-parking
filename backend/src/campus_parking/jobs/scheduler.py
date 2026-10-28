"""What the worker enqueues on its own clock. Due-logic is pure; the tick does the database part."""

import logging
from collections.abc import Collection
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def enqueue_warehouse_refresh(
    session: AsyncSession, settings: Settings, now: datetime
) -> None:
    """The 15-minute warehouse refresh is wired in with the warehouse job; until then it does
    nothing, so the scheduler already calls the place where it will go."""


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
    await enqueue_warehouse_refresh(session, settings, now)
    return created
