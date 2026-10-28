"""Run bookkeeping: mark a run running -> succeeded or failed, with counts and metrics."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus_parking.core import metrics
from campus_parking.core.clock import get_now
from campus_parking.jobs.models import PipelineRun

logger = logging.getLogger(__name__)

ERROR_CAP = 2000


@dataclass
class RunResult:
    """What a job fills in while it runs; written to the row when the run ends."""

    rows_read: int | None = None
    rows_written: int | None = None
    rows_rejected: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@asynccontextmanager
async def track(
    sessionmaker: async_sessionmaker[AsyncSession],
    run: PipelineRun,
    clock: Callable[[], datetime] = get_now,
) -> AsyncIterator[RunResult]:
    """Finish an already-running run: succeeded on a clean exit, failed (re-raised) on any error.

    A cancelled task is recorded as failed "interrupted". Uses its own session, so a rollback in
    the job never loses the final status.
    """
    result = RunResult()
    run_id, job = run.run_id, run.job
    try:
        yield result
    except BaseException as exc:
        text = "interrupted" if _cancelled(exc) else f"{type(exc).__name__}: {exc}"
        await _finish(sessionmaker, run_id, job, "failed", result, text[:ERROR_CAP], clock())
        raise
    await _finish(sessionmaker, run_id, job, "succeeded", result, None, clock())


def _cancelled(exc: BaseException) -> bool:
    return isinstance(exc, asyncio.CancelledError)


async def _finish(
    sessionmaker: async_sessionmaker[AsyncSession],
    run_id: UUID,
    job: str,
    status: str,
    result: RunResult,
    error: str | None,
    now: datetime,
) -> None:
    async with sessionmaker() as session:
        await session.execute(
            update(PipelineRun)
            .where(PipelineRun.run_id == run_id)
            .values(
                status=status,
                finished_at=now,
                rows_read=result.rows_read,
                rows_written=result.rows_written,
                rows_rejected=result.rows_rejected,
                metrics=result.metrics or None,
                error=error,
            )
        )
        await session.commit()
    metrics.pipeline_runs_total.labels(job, status).inc()
    logger.info("run finished", extra={"run_id": str(run_id), "job": job, "status": status})
