"""Operator run routes, the queue, close_day, the export and the worker on SQLite."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from campus_parking.core.clock import get_now
from campus_parking.core.db import Base
from campus_parking.jobs import queue
from campus_parking.jobs import worker as worker_module
from campus_parking.jobs.models import PipelineRun, ServiceDay
from campus_parking.jobs.worker import Worker
from campus_parking.main import create_app
from campus_parking.occupancy.models import LotOccupancy, Stay
from tests.conftest import make_settings
from tests.helpers import (
    ADMIN,
    INGEST,
    NOW,
    OPERATOR,
    REF_A,
    REF_B,
    REF_C,
    event,
    seed_registry,
)

DAY = date(2026, 10, 28)  # the local date of NOW
AFTER = NOW + timedelta(days=1)  # 11:00 local on 2026-10-29: DAY is over


@dataclass
class Env:
    client: httpx.AsyncClient
    app: FastAPI
    worker: Worker
    lake: Path
    clock: dict[str, datetime]

    async def post(self, events: list[dict[str, Any]], at: datetime | None = None) -> None:
        self.clock["now"] = at or NOW
        r = await self.client.post("/api/v1/events", json=events, headers=INGEST)
        assert r.status_code == 202
        assert r.json()["accepted"] == len(events)

    async def occupancy(self, lot: str = "principal") -> LotOccupancy:
        async with self.app.state.sessionmaker() as s:
            return (
                await s.execute(select(LotOccupancy).where(LotOccupancy.lot_id == lot))
            ).scalar_one()

    async def stays(self) -> dict[tuple[str, date], str]:
        async with self.app.state.sessionmaker() as s:
            rows = await s.execute(select(Stay.student_ref, Stay.service_date, Stay.status))
            return {(ref, day): status for ref, day, status in rows}

    async def runs(self, job: str | None = None) -> list[PipelineRun]:
        async with self.app.state.sessionmaker() as s:
            return list(await queue.list_runs(s, job, None, 100))

    def partition(self, dataset: str, key: str, day: date = DAY) -> Path:
        return self.lake / "raw" / dataset / f"{key}={day.isoformat()}"


@pytest.fixture
async def env(tmp_path: Path, client_for: Callable[[FastAPI], Any]) -> AsyncIterator[Env]:
    settings = make_settings(
        ingest_api_key=INGEST["X-API-Key"],
        admin_api_key=ADMIN["X-API-Key"],
        operator_api_key=OPERATOR["X-API-Key"],
        lake_dir=tmp_path / "lake",
        worker_heartbeat_file=tmp_path / "heartbeat",
        worker_poll_s=0.02,
    )
    app = create_app(settings)
    clock = {"now": NOW}
    app.dependency_overrides[get_now] = lambda: clock["now"]
    async with client_for(app) as client:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await seed_registry(client)
        worker = Worker(settings, app.state.sessionmaker, clock=lambda: clock["now"])
        yield Env(client, app, worker, tmp_path / "lake", clock)


async def post_run(client: httpx.AsyncClient, body: dict[str, Any], key: dict[str, str]) -> Any:
    return await client.post("/api/v1/operator/runs", json=body, headers=key)


async def test_post_run_queues_and_lists(env: Env) -> None:
    r = await post_run(env.client, {"job": "daily", "service_date": "2026-10-27"}, OPERATOR)
    assert r.status_code == 202
    body = r.json()
    assert (body["job"], body["status"], body["trigger"]) == ("daily", "queued", "operator")
    assert body["service_date"] == "2026-10-27"
    assert body["started_at"] is None

    one = await env.client.get(f"/api/v1/operator/runs/{body['run_id']}", headers=OPERATOR)
    assert one.status_code == 200
    assert one.json() == body

    # No date means yesterday in the service time zone.
    env.clock["now"] = NOW + timedelta(seconds=1)
    default = await post_run(env.client, {"job": "close_day"}, OPERATOR)
    assert default.json()["service_date"] == "2026-10-27"

    listing = await env.client.get("/api/v1/operator/runs", headers=OPERATOR)
    assert [x["job"] for x in listing.json()] == ["close_day", "daily"]  # newest first
    only = await env.client.get(
        "/api/v1/operator/runs",
        params={"job": "daily", "status": "queued", "limit": 1},
        headers=OPERATOR,
    )
    assert [x["run_id"] for x in only.json()] == [body["run_id"]]
    none = await env.client.get(
        "/api/v1/operator/runs", params={"status": "failed"}, headers=OPERATOR
    )
    assert none.json() == []


async def test_post_run_duplicate_is_409(env: Env) -> None:
    body = {"job": "close_day", "service_date": "2026-10-27"}
    assert (await post_run(env.client, body, OPERATOR)).status_code == 202
    again = await post_run(env.client, body, OPERATOR)
    assert again.status_code == 409
    assert again.json()["code"] == "run-already-active"
    # Another date or another job is not blocked.
    other = {"job": "close_day", "service_date": "2026-10-26"}
    assert (await post_run(env.client, other, OPERATOR)).status_code == 202
    assert (
        await post_run(env.client, {"job": "daily", "service_date": "2026-10-27"}, OPERATOR)
    ).status_code == 202


@pytest.mark.parametrize("day", ["2026-10-28", "2026-12-01"])
async def test_post_run_needs_a_past_date(env: Env, day: str) -> None:
    r = await post_run(env.client, {"job": "close_day", "service_date": day}, OPERATOR)
    assert r.status_code == 422
    assert r.json()["code"] == "validation-error"


async def test_post_run_rejects_other_jobs_and_unknown_run_is_404(env: Env) -> None:
    assert (await post_run(env.client, {"job": "nope"}, OPERATOR)).status_code == 422
    missing = await env.client.get(f"/api/v1/operator/runs/{uuid4()}", headers=OPERATOR)
    assert missing.status_code == 404
    assert missing.json()["code"] == "run-not-found"
    assert (
        await env.client.get("/api/v1/operator/runs/not-a-uuid", headers=OPERATOR)
    ).status_code == 422


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "/api/v1/operator/runs"),
        ("GET", "/api/v1/operator/runs"),
        ("GET", f"/api/v1/operator/runs/{uuid4()}"),
    ],
)
async def test_run_routes_need_the_operator_key(env: Env, method: str, url: str) -> None:
    kwargs: dict[str, Any] = {"json": {"job": "daily"}} if method == "POST" else {}
    for headers in ({}, INGEST, ADMIN, {"X-API-Key": "x" * 32}):
        r = await env.client.request(method, url, headers=headers, **kwargs)
        assert r.status_code == 401


async def test_claim_takes_the_oldest_queued_run_once(env: Env) -> None:
    sm = env.app.state.sessionmaker
    async with sm() as s:
        first, _ = await queue.enqueue(s, "close_day", date(2026, 10, 25), "cli", NOW)
        await queue.enqueue(s, "close_day", date(2026, 10, 26), "cli", NOW + timedelta(seconds=1))
    claimed = await env.worker.claim()
    assert claimed is not None
    assert (claimed.run_id, claimed.status) == (first.run_id, "running")
    second = await env.worker.claim()
    assert second is not None
    assert second.service_date == date(2026, 10, 26)
    assert await env.worker.claim() is None


async def test_enqueue_returns_the_active_run(env: Env) -> None:
    async with env.app.state.sessionmaker() as s:
        a, created = await queue.enqueue(s, "daily", DAY, "cli", NOW)
        b, created_again = await queue.enqueue(s, "daily", DAY, "schedule", NOW)
    assert (created, created_again) == (True, False)
    assert a.run_id == b.run_id


async def seed_day(env: Env) -> None:
    """A enters and never leaves, B enters and leaves, C is in the annex: 2 open stays."""
    await env.post(
        [
            event(1, "entry", REF_A, ago_min=120),
            event(2, "entry", REF_B, ago_min=100),
            event(3, "exit", REF_B, ago_min=60),
            event(4, "entry", REF_C, ago_min=30, lot="anexo"),
        ]
    )
    assert (await env.occupancy()).occupied == 1
    assert (await env.occupancy("anexo")).occupied == 1


async def test_daily_closes_pending_days_and_exports(env: Env) -> None:
    await seed_day(env)
    earlier = event(5, "entry", REF_B, ago_min=24 * 60 + 30)  # 2026-10-27 local
    await env.post([earlier])
    before = (await env.occupancy()).version

    env.clock["now"] = AFTER
    run = await env.worker.run_direct("daily", DAY)
    assert run is not None

    assert (await env.occupancy()).occupied == 0
    assert (await env.occupancy("anexo")).occupied == 0
    assert (await env.occupancy()).version > before  # the live poller sees it
    stays = await env.stays()
    assert set(stays.values()) == {"closed", "closed_by_reconciliation"}
    async with env.app.state.sessionmaker() as s:
        closed = {d for d in await s.scalars(select(ServiceDay.service_date))}
        reconciled = (
            await s.scalars(select(Stay).where(Stay.status == "closed_by_reconciliation"))
        ).all()
    assert closed == {date(2026, 10, 27), DAY}
    # ended at the next local midnight: 2026-10-29 00:00 in UTC-3
    assert {s.ended_at for s in reconciled if s.service_date == DAY} == {
        datetime(2026, 10, 29, 3, 0, tzinfo=UTC)
    }

    parent = next(r for r in await env.runs("daily"))
    assert parent.status == "succeeded"
    children = sorted(await env.runs("close_day"), key=lambda r: r.service_date)
    assert [c.service_date for c in children] == [date(2026, 10, 27), DAY]
    assert all(c.parent_run_id == parent.run_id and c.status == "succeeded" for c in children)
    assert children[1].metrics["closed_by_reconciliation"] == 2
    assert children[1].metrics["export_status"] == "written"
    assert children[1].metrics["drift"] == {}

    table = pq.read_table(env.partition("gate_events", "service_date") / "part-00000.parquet")
    assert table.num_rows == 4
    manifest = json.loads(
        (env.partition("gate_events", "service_date") / "_manifest.json").read_text("utf-8")
    )
    assert (manifest["dataset"], manifest["row_count"], manifest["source_row_count"]) == (
        "gate_events",
        4,
        4,
    )
    assert env.partition("ingest_rejects", "received_date").joinpath("part-00000.parquet").is_file()


async def test_rerun_changes_nothing(env: Env) -> None:
    await seed_day(env)
    env.clock["now"] = AFTER
    assert await env.worker.run_direct("daily", DAY) is not None
    version = (await env.occupancy()).version
    stays = await env.stays()
    env.clock["now"] = AFTER + timedelta(hours=1)
    assert await env.worker.run_direct("daily", DAY) is not None

    assert (await env.occupancy()).version == version
    assert await env.stays() == stays
    second = max(await env.runs("close_day"), key=lambda r: r.requested_at)
    assert second.metrics["changes"] == 0
    assert second.metrics["closed_by_reconciliation"] == 0
    assert second.metrics["export_status"] == "unchanged"


async def test_drift_is_fixed_and_recorded(env: Env) -> None:
    await seed_day(env)
    async with env.app.state.sessionmaker() as s:
        row = await s.get(LotOccupancy, "principal")
        assert row is not None
        row.occupied = 5  # the counter drifted: truth is 1 open stay
        await s.commit()
    env.clock["now"] = AFTER
    await env.worker.run_direct("close_day", DAY)
    assert (await env.occupancy()).occupied == 0
    run = (await env.runs("close_day"))[0]
    assert run.metrics["drift"] == {"principal": -4}
    assert run.metrics["changes"] == 2 + 4


async def test_close_day_refuses_a_day_that_is_not_over(env: Env) -> None:
    env.clock["now"] = NOW
    with pytest.raises(Exception, match="not over"):
        await env.worker.run_direct("close_day", DAY)
    (run,) = await env.runs("close_day")
    assert run.status == "failed"
    assert "not over" in (run.error or "")


async def test_late_event_keeps_the_closed_semantics_and_the_next_daily_reprocesses(
    env: Env,
) -> None:
    await seed_day(env)
    env.clock["now"] = AFTER
    await env.worker.run_direct("daily", DAY)
    async with env.app.state.sessionmaker() as s:
        version = (await s.get(LotOccupancy, "principal")).version  # type: ignore[union-attr]

    # Received an hour after the close; the entry belongs to the closed day.
    late = event(9, "entry", REF_C, ago_min=45)
    await env.post([late], at=AFTER + timedelta(hours=1))
    stays = await env.stays()
    assert stays[(REF_C, DAY)] == "closed_by_reconciliation"
    assert (await env.occupancy()).occupied == 0
    assert (await env.occupancy()).version >= version

    env.clock["now"] = AFTER + timedelta(hours=2)
    assert await pending(env) == [DAY]

    await env.worker.run_direct("daily", date(2026, 10, 28))
    latest = max(await env.runs("close_day"), key=lambda r: r.requested_at)
    assert latest.metrics["late_events"] == 1
    assert latest.metrics["export_status"] == "written"
    table = pq.read_table(env.partition("gate_events", "service_date") / "part-00000.parquet")
    assert table.num_rows == 5

    env.clock["now"] = AFTER + timedelta(hours=3)
    assert await pending(env) == []


async def test_daily_marks_failed_days_and_goes_on(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    await env.post([event(5, "entry", REF_B, ago_min=24 * 60 + 30)])
    env.clock["now"] = AFTER
    real = worker_module.close_day

    async def flaky(sm: Any, settings: Any, day: date, *rest: Any) -> None:
        if day == date(2026, 10, 27):
            raise RuntimeError("boom")
        await real(sm, settings, day, *rest)

    monkeypatch.setattr(worker_module, "close_day", flaky)
    with pytest.raises(RuntimeError, match="1 of 2 days failed"):
        await env.worker.run_direct("daily", DAY)
    parent = (await env.runs("daily"))[0]
    assert parent.status == "failed"
    assert parent.metrics["failed"] == ["2026-10-27"]
    assert parent.metrics["closed"] == ["2026-10-28"]
    failed = [r for r in await env.runs("close_day") if r.status == "failed"]
    assert [r.error for r in failed] == ["RuntimeError: boom"]


async def test_daily_skips_a_date_that_is_already_active(env: Env) -> None:
    async with env.app.state.sessionmaker() as s:
        await queue.enqueue(s, "close_day", DAY, "operator", NOW)
    env.clock["now"] = AFTER
    await env.worker.run_direct("daily", DAY)
    parent = (await env.runs("daily"))[0]
    assert parent.status == "succeeded"
    assert parent.metrics["skipped"] == ["2026-10-28"]


async def test_export_mode_reexports_without_closing(env: Env) -> None:
    await seed_day(env)
    env.clock["now"] = AFTER
    run = await env.worker.run_direct("close_day", DAY, mode="export")
    assert run is not None
    assert (await env.occupancy()).occupied == 1  # not closed
    assert env.partition("gate_events", "service_date").is_dir()
    env.clock["now"] = NOW
    with pytest.raises(Exception, match="not over"):
        await env.worker.run_direct("close_day", DAY, mode="export")


async def test_run_direct_refuses_when_one_is_active(env: Env) -> None:
    async with env.app.state.sessionmaker() as s:
        await queue.enqueue(s, "close_day", DAY, "operator", NOW)
    assert await env.worker.run_direct("close_day", DAY) is None


async def test_unknown_job_fails_the_run(env: Env) -> None:
    run = PipelineRun(run_id=uuid4(), job="nope", mode=None, service_date=DAY)
    with pytest.raises(RuntimeError, match="unknown job"):
        await env.worker.execute(run)


async def test_warehouse_runs_in_its_mode(env: Env, fake_dbt: list[str]) -> None:
    await env.worker.run_direct("warehouse", DAY, mode="full")
    await env.worker.run_direct("warehouse", DAY - timedelta(days=1))
    assert fake_dbt == ["full", "incremental"]
    runs = await env.runs("warehouse")
    assert sorted(r.metrics["mode"] for r in runs) == ["full", "incremental"]
    assert {r.status for r in runs} == {"succeeded"}


async def test_daily_ends_with_a_full_warehouse_child(env: Env, fake_dbt: list[str]) -> None:
    env.clock["now"] = AFTER
    await env.worker.run_direct("daily", DAY)
    parent = (await env.runs("daily"))[0]
    (child,) = await env.runs("warehouse")
    assert (child.mode, child.status, child.parent_run_id) == ("full", "succeeded", parent.run_id)
    assert (parent.status, parent.metrics["warehouse"]) == ("succeeded", "succeeded")
    assert fake_dbt == ["full"]


async def test_a_failed_warehouse_child_fails_daily(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: Any) -> None:
        raise RuntimeError("dbt broke")

    monkeypatch.setattr(worker_module, "run_warehouse", broken)
    env.clock["now"] = AFTER
    with pytest.raises(RuntimeError, match="warehouse refresh failed"):
        await env.worker.run_direct("daily", DAY)
    parent = (await env.runs("daily"))[0]
    assert (parent.status, parent.metrics["warehouse"]) == ("failed", "failed")
    (child,) = await env.runs("warehouse")
    assert child.error == "RuntimeError: dbt broke"


async def test_daily_skips_the_warehouse_child_when_one_is_active(env: Env) -> None:
    async with env.app.state.sessionmaker() as s:
        await queue.enqueue(s, "warehouse", DAY, "operator", NOW, "incremental")
    env.clock["now"] = AFTER
    await env.worker.run_direct("daily", DAY)
    assert (await env.runs("daily"))[0].metrics["warehouse"] == "skipped"


async def test_abandoned_runs_are_marked_failed_at_startup(env: Env) -> None:
    async with env.app.state.sessionmaker() as s:
        running, _ = await queue.add_run(
            s, job="close_day", service_date=DAY, trigger="cli", now=NOW, status="running"
        )
        queued, _ = await queue.enqueue(s, "daily", DAY, "cli", NOW)
    assert await env.worker.abandon_leftovers() == 1
    by_id = {r.run_id: r for r in await env.runs()}
    assert (by_id[running.run_id].status, by_id[running.run_id].error) == ("failed", "abandoned")
    assert by_id[running.run_id].finished_at is not None
    assert by_id[queued.run_id].status == "queued"
    assert await env.worker.abandon_leftovers() == 0


async def test_tick_enqueues_daily_once_after_the_run_time(env: Env) -> None:
    before_time = datetime(2026, 10, 29, 3, 10, tzinfo=UTC)  # 00:10 local
    env.clock["now"] = before_time
    await env.worker.tick()
    assert await env.runs("daily") == []
    env.clock["now"] = datetime(2026, 10, 29, 3, 40, tzinfo=UTC)  # 00:40 local
    await env.worker.tick()
    await env.worker.tick()
    (run,) = await env.runs("daily")
    assert (run.job, run.trigger, run.service_date) == ("daily", "schedule", DAY)


async def test_tick_enqueues_the_warehouse_refresh_every_interval(env: Env) -> None:
    await env.worker.tick()
    (first,) = await env.runs("warehouse")
    assert (first.mode, first.trigger, first.service_date) == ("incremental", "schedule", DAY)
    env.clock["now"] = NOW + timedelta(minutes=14)
    await env.worker.tick()  # not due yet
    assert len(await env.runs("warehouse")) == 1
    env.clock["now"] = NOW + timedelta(minutes=15)
    await env.worker.tick()  # due, but the first one is still queued for the same date
    assert len(await env.runs("warehouse")) == 1
    async with env.app.state.sessionmaker() as s:
        assert await queue.claim(s, NOW) is not None
    await env.worker.execute((await env.runs("warehouse"))[0])
    env.clock["now"] = NOW + timedelta(minutes=30)
    await env.worker.tick()
    assert len(await env.runs("warehouse")) == 2


async def test_serve_claims_a_queued_run_and_stops(env: Env) -> None:
    env.clock["now"] = AFTER
    r = await post_run(env.client, {"job": "close_day", "service_date": "2026-10-28"}, OPERATOR)
    run_id = r.json()["run_id"]
    stop = asyncio.Event()
    task = asyncio.create_task(env.worker.serve(stop))
    status = "queued"
    for _ in range(600):
        got = await env.client.get(f"/api/v1/operator/runs/{run_id}", headers=OPERATOR)
        status = got.json()["status"]
        if status in ("succeeded", "failed"):
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert status == "succeeded"
    assert (env.worker.settings.worker_heartbeat_file).exists()
    # the scheduler tick ran as well: daily for yesterday (2026-10-28) is queued by then
    assert any(x.job == "daily" for x in await env.runs())


async def test_stop_interrupts_a_run_that_outlives_the_grace(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    async def slow(self: Worker, run: PipelineRun, result: Any) -> None:
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(Worker, "_close_day", slow)
    monkeypatch.setattr(worker_module, "STOP_GRACE_S", 0.05)
    env.clock["now"] = AFTER
    await post_run(env.client, {"job": "close_day", "service_date": "2026-10-28"}, OPERATOR)
    stop = asyncio.Event()
    task = asyncio.create_task(env.worker.serve(stop))
    await asyncio.wait_for(started.wait(), 5)
    stop.set()
    await asyncio.wait_for(task, 5)
    run = next(r for r in await env.runs("close_day"))
    assert (run.status, run.error) == ("failed", "interrupted")


async def test_loop_errors_do_not_stop_the_worker(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def broken(self: Worker) -> None:
        nonlocal calls
        calls += 1
        raise OSError("database down")

    monkeypatch.setattr(Worker, "tick", broken)
    stop = asyncio.Event()
    task = asyncio.create_task(env.worker.serve(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert calls >= 2


async def pending(env: Env) -> list[date]:
    from campus_parking.jobs.close_day import pending_days

    async with env.app.state.sessionmaker() as s:
        return await pending_days(s, env.worker.settings, env.clock["now"])


async def test_a_late_event_stamped_before_the_close_is_still_found(env: Env) -> None:
    """received_at can be older than closed_at for an event committed after the export: only the
    exported count notices it."""
    await seed_day(env)
    env.clock["now"] = AFTER
    await env.worker.run_direct("daily", DAY)
    assert await pending(env) == []
    await env.post([event(9, "entry", REF_C, ago_min=45)], at=AFTER - timedelta(minutes=10))
    env.clock["now"] = AFTER
    assert await pending(env) == [DAY]
    await env.worker.run_direct("daily", DAY)
    assert await pending(env) == []


async def test_a_failed_export_leaves_the_date_pending(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from campus_parking.jobs import close_day as close_day_module

    await seed_day(env)
    env.clock["now"] = AFTER

    async def broken(*_: Any) -> None:
        raise OSError("disk full")

    real = close_day_module.export_day
    monkeypatch.setattr(close_day_module, "export_day", broken)
    with pytest.raises(OSError, match="disk full"):
        await env.worker.run_direct("close_day", DAY)
    assert DAY in await pending(env)
    monkeypatch.setattr(close_day_module, "export_day", real)
    await env.worker.run_direct("close_day", DAY)
    assert await pending(env) == []


async def test_post_run_passes_the_mode_and_validates_it(env: Env) -> None:
    ok = await post_run(
        env.client, {"job": "close_day", "service_date": "2026-10-27", "mode": "export"}, OPERATOR
    )
    assert (ok.status_code, ok.json()["mode"]) == (202, "export")
    bad_job = await post_run(env.client, {"job": "daily", "mode": "export"}, OPERATOR)
    assert bad_job.status_code == 422
    assert (
        await post_run(env.client, {"job": "close_day", "mode": "x"}, OPERATOR)
    ).status_code == 422
    wh = await post_run(env.client, {"job": "warehouse", "service_date": "2026-10-26"}, OPERATOR)
    assert (wh.status_code, wh.json()["mode"]) == (202, "incremental")
    full = await post_run(env.client, {"job": "warehouse", "mode": "full"}, OPERATOR)
    assert (full.status_code, full.json()["mode"]) == (202, "full")
    assert full.json()["service_date"] == "2026-10-28"  # warehouse defaults to today


async def test_warehouse_takes_today_or_the_past_but_never_the_future(env: Env) -> None:
    today = await post_run(env.client, {"job": "warehouse", "service_date": "2026-10-28"}, OPERATOR)
    assert today.status_code == 202
    future = await post_run(
        env.client, {"job": "warehouse", "service_date": "2026-10-29"}, OPERATOR
    )
    assert (future.status_code, future.json()["code"]) == (422, "validation-error")
    # daily and close_day keep the past-date rule.
    for job in ("daily", "close_day"):
        r = await post_run(env.client, {"job": job, "service_date": "2026-10-28"}, OPERATOR)
        assert r.status_code == 422
    for body in ({"job": "warehouse", "mode": "export"}, {"job": "close_day", "mode": "full"}):
        assert (await post_run(env.client, body, OPERATOR)).status_code == 422
    unknown = await post_run(env.client, {"job": "close_day", "colour": 1}, OPERATOR)
    assert unknown.status_code == 422
