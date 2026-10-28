"""Queue, close_day and the export swap on Postgres, on a schema built by the real migrations."""

import asyncio
import hashlib
import json
import threading
from collections.abc import AsyncIterator, Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from alembic import command
from campus_parking.cli import alembic_config
from campus_parking.jobs import archive_contract as contract
from campus_parking.jobs import queue
from campus_parking.jobs.export import MANIFEST, PART, publish_partition
from campus_parking.jobs.worker import Worker
from tests.helpers import INGEST, NOW, REF_A, REF_B, REF_C, event, seed_registry
from tests.integration.test_ingestion_pg import RECOUNT, build_app, rows
from tests.unit.test_archive_contract import row as archive_row

DAY = date(2026, 10, 28)
AFTER = NOW + timedelta(days=1)


@pytest.fixture
def migrated(pg_database: str) -> str:
    # Sync on purpose: alembic's async env.py calls asyncio.run, which cannot run inside the
    # event loop of an async fixture.
    command.upgrade(alembic_config(), "head")
    return pg_database


@pytest.fixture
async def pg(
    migrated: str, client_for: Callable[[Any], Any], tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI, Worker]]:
    app = build_app(
        migrated,
        lake_dir=tmp_path / "lake",
        worker_heartbeat_file=tmp_path / "hb",
        ingest_max_concurrency=8,
    )
    async with client_for(app) as client:
        await seed_registry(client, capacity=3000)
        yield client, app, Worker(app.state.settings, app.state.sessionmaker, lambda: AFTER)


async def test_concurrent_enqueue_creates_one_run(
    pg: tuple[httpx.AsyncClient, FastAPI, Worker],
) -> None:
    _, app, _ = pg

    async def one() -> bool:
        async with app.state.sessionmaker() as s:
            return (await queue.enqueue(s, "daily", DAY, "operator", NOW))[1]

    created = await asyncio.gather(*(one() for _ in range(8)))
    assert sum(created) == 1
    assert await rows(app, "SELECT count(*) FROM pipeline_runs") == [(1,)]


async def test_two_workers_never_claim_the_same_run(
    pg: tuple[httpx.AsyncClient, FastAPI, Worker],
) -> None:
    _, app, _ = pg
    async with app.state.sessionmaker() as s:
        for n in range(24):
            await queue.enqueue(
                s, "close_day", DAY - timedelta(days=n + 1), "cli", NOW + timedelta(seconds=n)
            )

    async def worker_loop() -> list[UUID]:
        claimed: list[UUID] = []
        while True:
            async with app.state.sessionmaker() as s:
                run = await queue.claim(s, NOW)
            if run is None:
                return claimed
            claimed.append(run.run_id)
            await asyncio.sleep(0)

    first, second = await asyncio.gather(worker_loop(), worker_loop())
    assert len(first) + len(second) == 24
    assert set(first).isdisjoint(second)
    assert await rows(app, "SELECT count(*) FROM pipeline_runs WHERE status = 'running'") == [(24,)]


async def test_close_day_under_concurrent_ingest_keeps_occupancy_equal_to_the_recount(
    pg: tuple[httpx.AsyncClient, FastAPI, Worker],
) -> None:
    client, app, worker = pg
    students = [f"stu_{n:012x}" for n in range(1, 61)]
    resp = await client.post(
        "/api/v1/admin/students/bulk",
        json={"student_refs": students},
        headers={"X-API-Key": "a" * 32},
    )
    assert resp.status_code == 200
    batches = [
        [
            event(1000 + 10 * b + i, "entry", students[10 * b + i], ago_min=200 - b)
            for i in range(10)
        ]
        for b in range(6)
    ]
    assert (await client.post("/api/v1/events", json=batches[0], headers=INGEST)).status_code == 202

    async def ingest(batch: list[dict[str, Any]]) -> int:
        deadline = (
            asyncio.get_running_loop().time() + 10
        )  # the test checks the invariant, not speed
        while True:
            r = await client.post("/api/v1/events", json=batch, headers=INGEST)
            if r.status_code != 429 or asyncio.get_running_loop().time() > deadline:
                return r.status_code
            await asyncio.sleep(0.1)

    results = await asyncio.gather(
        worker.run_direct("close_day", DAY), *(ingest(b) for b in batches[1:])
    )
    assert results[1:] == [202] * 5
    stored = {
        lot: n for lot, n in await rows(app, "SELECT lot_id, occupied FROM lot_occupancy") if n
    }
    recount = {lot: n for lot, n in await rows(app, RECOUNT)}
    assert stored == recount
    # whichever side of the close each batch landed on, no stay of the day is left open
    assert recount == {}
    assert await rows(app, "SELECT count(*) FROM stays WHERE status = 'open'") == [(0,)]


async def test_close_day_writes_the_archive_on_postgres(
    pg: tuple[httpx.AsyncClient, FastAPI, Worker], tmp_path: Path
) -> None:
    client, _, worker = pg
    body = [
        event(1, "entry", REF_A, ago_min=90),
        event(2, "entry", REF_B, ago_min=80),
        event(3, "exit", REF_B, ago_min=20),
        event(4, "entry", REF_C, ago_min=10),
    ]
    assert (await client.post("/api/v1/events", json=body, headers=INGEST)).status_code == 202
    await worker.run_direct("close_day", DAY)
    part = tmp_path / "lake" / "raw" / "gate_events" / "service_date=2026-10-28"
    assert pq.read_table(part / PART).num_rows == 4
    await worker.run_direct("daily", DAY)  # a rerun finds the same rows
    manifest = json.loads((part / MANIFEST).read_text("utf-8"))
    assert manifest["row_count"] == 4


def test_the_swap_never_exposes_a_half_written_partition(tmp_path: Path) -> None:
    final = tmp_path / "raw" / "gate_events" / "service_date=2026-10-28"
    sizes = (40, 400)

    def table(n: int) -> pa.Table:
        return pa.Table.from_pylist(
            [archive_row(i % 50 + 1, event_id=str(UUID(int=i + n * 1000))) for i in range(n)],
            schema=contract.GATE_EVENTS_SCHEMA,
        )

    tables = [table(n) for n in sizes]
    publish_partition(final, "gate_events", uuid4(), tables[0], sizes[0])
    stop = threading.Event()
    problems: list[str] = []
    reads = 0

    def reader() -> None:
        nonlocal reads
        while not stop.is_set():
            try:
                data = (final / PART).read_bytes()
                meta = json.loads((final / MANIFEST).read_text("utf-8"))
            except (FileNotFoundError, PermissionError):
                continue  # between the two renames the partition is simply absent
            try:
                n = pq.read_table(pa.BufferReader(data)).num_rows
            except Exception as exc:  # noqa: BLE001 - any parse failure is the bug being hunted
                problems.append(f"unreadable parquet: {exc}")
                return
            if n not in sizes or meta["row_count"] not in sizes:
                problems.append(f"unexpected sizes {n} / {meta['row_count']}")
            if hashlib.sha256(data).hexdigest() != meta["sha256"] and n == meta["row_count"]:
                problems.append("manifest and file of the same size disagree")
            reads += 1

    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        for i in range(30):
            n = sizes[(i + 1) % 2]
            publish_partition(final, "gate_events", uuid4(), tables[(i + 1) % 2], n)
    finally:
        stop.set()
        for t in readers:
            t.join()
    assert problems == []
    assert reads > 0
    assert sorted(p.name for p in final.parent.iterdir()) == ["service_date=2026-10-28"]


async def test_pipeline_runs_checks_reject_bad_values(
    pg: tuple[httpx.AsyncClient, FastAPI, Worker],
) -> None:
    _, app, _ = pg
    insert = (
        "INSERT INTO pipeline_runs (run_id, job, trigger, service_date, status, requested_at) "
        "VALUES (gen_random_uuid(), '{job}', 'cli', '2026-10-28', '{status}', now())"
    )
    async with app.state.engine.begin() as conn:
        await conn.execute(text(insert.format(job="warehouse", status="queued")))
    for job, status in (("export", "queued"), ("daily", "paused")):
        with pytest.raises(Exception, match="violates check constraint"):
            async with app.state.engine.begin() as conn:
                await conn.execute(text(insert.format(job=job, status=status)))
