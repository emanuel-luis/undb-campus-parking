"""The worker process: one asyncio loop, one job at a time, runs claimed from `pipeline_runs`."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable
from datetime import date, datetime, timedelta

from prometheus_client import start_http_server
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus_parking.core import metrics
from campus_parking.core.clock import get_now, service_date
from campus_parking.core.db import create_engine, create_sessionmaker
from campus_parking.core.problems import DayNotClosed
from campus_parking.core.settings import Settings
from campus_parking.jobs import queue, scheduler
from campus_parking.jobs.close_day import close_day, export_into, pending_days, require_past
from campus_parking.jobs.models import PipelineRun
from campus_parking.jobs.runs import RunResult, track

logger = logging.getLogger(__name__)

SCHEDULER_TICK_S = 30.0
STOP_GRACE_S = 25.0  # SIGTERM to exit stays under 30 s
HEARTBEAT_S = 5.0


class Worker:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = get_now,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.clock = clock

    async def abandon_leftovers(self) -> int:
        """Runs still `running` belong to a worker that is gone (this one is the only worker)."""
        async with self.sessionmaker() as session:
            count = await queue.abandon_running(session, self.clock())
        if count:
            logger.warning("abandoned runs marked failed", extra={"count": count})
        return count

    async def tick(self) -> None:
        async with self.sessionmaker() as session:
            await scheduler.tick(session, self.settings, self.clock())

    async def claim(self) -> PipelineRun | None:
        async with self.sessionmaker() as session:
            return await queue.claim(session, self.clock())

    async def execute(self, run: PipelineRun) -> RunResult:
        """Run a claimed (running) run to its end; the run row records the outcome."""
        async with track(self.sessionmaker, run, self.clock) as result:
            if run.job == "close_day":
                await self._close_day(run, result)
            elif run.job == "daily":
                await self._daily(run, result)
            else:
                raise RuntimeError(f"job {run.job!r} is not available yet")
        return result

    async def _close_day(self, run: PipelineRun, result: RunResult) -> None:
        now = self.clock()
        if run.mode == "export":
            require_past(run.service_date, now, self.settings)
            await export_into(
                self.sessionmaker, self.settings, run.service_date, run.run_id, result
            )
        else:
            await close_day(
                self.sessionmaker, self.settings, run.service_date, run.run_id, now, result
            )

    async def _daily(self, run: PipelineRun, result: RunResult) -> None:
        """close_day, as a child run, for every pending date in date order."""
        async with self.sessionmaker() as session:
            days = await pending_days(session, self.settings, self.clock())
        days = sorted({*days, run.service_date})
        done: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        rows_read = rows_written = 0
        for day in days:
            async with self.sessionmaker() as session:
                child, created = await queue.add_run(
                    session,
                    job="close_day",
                    service_date=day,
                    trigger=run.trigger,
                    now=self.clock(),
                    status="running",
                    parent_run_id=run.run_id,
                )
            if not created:  # another close_day for this date is queued or running
                skipped.append(day.isoformat())
                continue
            try:
                child_result = await self.execute(child)
            except Exception:  # noqa: BLE001 - the child row holds the error; go on with the rest
                failed.append(day.isoformat())
                continue
            done.append(day.isoformat())
            rows_read += child_result.rows_read or 0
            rows_written += child_result.rows_written or 0
        result.rows_read, result.rows_written = rows_read, rows_written
        result.metrics.update(closed=done, skipped=skipped, failed=failed)
        if failed:
            raise RuntimeError(f"{len(failed)} of {len(days)} days failed: {', '.join(failed)}")

    async def run_direct(
        self, job: str, day: date, trigger: str = "cli", mode: str | None = None
    ) -> PipelineRun | None:
        """Run a job now, in this process, as a recorded run. None when one is already active.
        Raises what the job raised (the run row says failed)."""
        async with self.sessionmaker() as session:
            run, created = await queue.add_run(
                session,
                job=job,
                service_date=day,
                trigger=trigger,
                now=self.clock(),
                status="running",
                mode=mode,
            )
        if not created:
            return None
        await self.execute(run)
        return run

    async def _process(self, run: PipelineRun, stop: asyncio.Event) -> None:
        """Let the run finish; on a stop request give it STOP_GRACE_S, then cancel it (the run
        is recorded as failed "interrupted")."""
        task = asyncio.ensure_future(self.execute(run))
        stopper = asyncio.ensure_future(stop.wait())
        await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done():
            await asyncio.wait({task}, timeout=STOP_GRACE_S)
            if not task.done():
                task.cancel()
        stopper.cancel()
        (outcome,) = await asyncio.gather(task, return_exceptions=True)
        if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
            logger.error("run failed", extra={"run_id": str(run.run_id), "job": run.job})

    async def _beat(self) -> None:
        path = self.settings.worker_heartbeat_file
        while True:
            with contextlib.suppress(OSError):
                path.touch()
            await asyncio.sleep(HEARTBEAT_S)

    async def serve(self, stop: asyncio.Event) -> None:
        """Claim loop and scheduler until `stop` is set."""
        await self.abandon_leftovers()
        beat = asyncio.create_task(self._beat())
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        try:
            while not stop.is_set():
                run: PipelineRun | None = None
                try:
                    if loop.time() >= next_tick:
                        await self.tick()
                        next_tick = loop.time() + SCHEDULER_TICK_S
                    run = await self.claim()
                except Exception:
                    logger.exception("worker loop error")
                    next_tick = loop.time()
                if run is not None:
                    await self._process(run, stop)
                    continue
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.settings.worker_poll_s)
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)


def _install_stop(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops have no add_signal_handler
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def run_worker(settings: Settings) -> int:
    engine = create_engine(settings)
    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port, registry=metrics.registry)
    stop = asyncio.Event()
    _install_stop(stop)
    try:
        await Worker(settings, create_sessionmaker(engine)).serve(stop)
    finally:
        await engine.dispose()
    return 0


async def run_once(settings: Settings, job: str, day: date | None, trigger: str = "cli") -> int:
    """`worker --once`, `close-day` and `export`: one run in this process; exit code for the CLI.
    0 succeeded, 1 failed or already active, 2 the date is not over yet."""
    engine = create_engine(settings)
    worker = Worker(settings, create_sessionmaker(engine))
    try:
        target = day or service_date(worker.clock(), settings.tzinfo) - timedelta(days=1)
        mode = "export" if job == "export" else None
        try:
            run = await worker.run_direct(
                "close_day" if job == "export" else job, target, trigger, mode
            )
        except DayNotClosed as exc:
            logger.error(exc.detail)
            return 2
        except Exception as exc:  # noqa: BLE001 - reported as an exit code; the run row has it
            logger.error("run failed", extra={"error": f"{type(exc).__name__}: {exc}"})
            return 1
        if run is None:
            logger.error("a run for this job and date is already queued or running")
            return 1
        return 0
    finally:
        await engine.dispose()
