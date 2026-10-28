"""The run queue on `pipeline_runs`: enqueue, claim and list."""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core import metrics
from campus_parking.core.db import insert_ignore
from campus_parking.jobs.models import PipelineRun

ACTIVE = ("queued", "running")


async def add_run(
    session: AsyncSession,
    *,
    job: str,
    service_date: date,
    trigger: str,
    now: datetime,
    status: str = "queued",
    mode: str | None = None,
    parent_run_id: UUID | None = None,
) -> tuple[PipelineRun, bool]:
    """Insert a run unless one is already queued or running for (job, service_date).

    Returns (run, created); when not created, `run` is the active one that blocked the insert.
    The partial unique index decides, so two writers cannot both win.
    """
    dialect = session.get_bind().dialect.name
    run_id = uuid4()
    inserted = await session.scalar(
        insert_ignore(dialect, PipelineRun)
        .values(
            run_id=run_id,
            parent_run_id=parent_run_id,
            job=job,
            mode=mode,
            trigger=trigger,
            service_date=service_date,
            status=status,
            requested_at=now,
            started_at=now if status == "running" else None,
        )
        .returning(PipelineRun.run_id)
    )
    if inserted is not None:
        await session.commit()
        return await _get(session, run_id), True
    await session.rollback()
    active = await session.scalar(
        select(PipelineRun).where(
            PipelineRun.job == job,
            PipelineRun.service_date == service_date,
            PipelineRun.status.in_(ACTIVE),
        )
    )
    if active is None:  # finished between the insert and the lookup: try once more
        return await add_run(
            session,
            job=job,
            service_date=service_date,
            trigger=trigger,
            now=now,
            status=status,
            mode=mode,
            parent_run_id=parent_run_id,
        )
    return active, False


async def _get(session: AsyncSession, run_id: UUID) -> PipelineRun:
    return (
        await session.execute(select(PipelineRun).where(PipelineRun.run_id == run_id))
    ).scalar_one()


async def enqueue(
    session: AsyncSession,
    job: str,
    service_date: date,
    trigger: str,
    now: datetime,
    mode: str | None = None,
) -> tuple[PipelineRun, bool]:
    return await add_run(
        session, job=job, service_date=service_date, trigger=trigger, now=now, mode=mode
    )


async def claim(session: AsyncSession, now: datetime) -> PipelineRun | None:
    """Take the oldest queued run, marking it running, in one statement.

    On Postgres the inner SELECT skips rows another worker has locked, so two workers never get
    the same run. SQLite has one writer at a time, which gives the same guarantee.
    """
    oldest = (
        select(PipelineRun.run_id)
        .where(PipelineRun.status == "queued")
        .order_by(PipelineRun.requested_at, PipelineRun.run_id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    run_id = await session.scalar(
        update(PipelineRun)
        .where(PipelineRun.run_id == oldest, PipelineRun.status == "queued")
        .values(status="running", started_at=now)
        .returning(PipelineRun.run_id)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return None if run_id is None else await _get(session, run_id)


async def abandon_running(session: AsyncSession, now: datetime) -> int:
    """Mark leftover running rows failed: their worker is gone (called once at startup)."""
    result = await session.execute(
        update(PipelineRun)
        .where(PipelineRun.status == "running")
        .values(status="failed", finished_at=now, error="abandoned")
        .returning(PipelineRun.job)
    )
    jobs = result.scalars().all()
    await session.commit()
    for job in jobs:
        metrics.pipeline_runs_total.labels(job, "failed").inc()
    return len(jobs)


async def list_runs(
    session: AsyncSession, job: str | None, status: str | None, limit: int
) -> Sequence[PipelineRun]:
    stmt = select(PipelineRun).order_by(PipelineRun.requested_at.desc(), PipelineRun.run_id.desc())
    if job is not None:
        stmt = stmt.where(PipelineRun.job == job)
    if status is not None:
        stmt = stmt.where(PipelineRun.status == status)
    return (await session.scalars(stmt.limit(limit))).all()
