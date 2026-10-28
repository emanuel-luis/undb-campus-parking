"""Ingestion and occupancy on Postgres, on a schema built by the real migrations."""

import asyncio
import random
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from alembic import command
from campus_parking.cli import alembic_config
from campus_parking.core.clock import get_now
from campus_parking.main import create_app
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
    iso,
    seed_registry,
)

URL = "/api/v1/events"
RECOUNT = "SELECT lot_id, count(*) FROM stays WHERE status = 'open' GROUP BY lot_id"
OCCUPIED = "SELECT lot_id, occupied FROM lot_occupancy"


@pytest.fixture
def migrated(pg_database: str) -> str:
    command.upgrade(alembic_config(), "head")
    return pg_database


def build_app(url: str, **overrides: Any) -> FastAPI:
    app = create_app(
        make_settings(
            database_url=url,
            ingest_api_key=INGEST["X-API-Key"],
            admin_api_key=ADMIN["X-API-Key"],
            operator_api_key=OPERATOR["X-API-Key"],
            **overrides,
        )
    )
    app.dependency_overrides[get_now] = lambda: NOW
    return app


@pytest.fixture
async def pg(
    migrated: str, client_for: Callable[[Any], Any]
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    app = build_app(migrated)
    async with client_for(app) as client:
        await seed_registry(client, capacity=3000)
        yield client, app


async def rows(app: FastAPI, sql: str) -> list[Any]:
    async with app.state.engine.connect() as conn:
        return list((await conn.execute(text(sql))).all())


async def occupancy_equals_recount(app: FastAPI) -> dict[str, int]:
    recount = {lot: n for lot, n in await rows(app, RECOUNT)}
    stored = {lot: n for lot, n in await rows(app, OCCUPIED) if n}
    assert stored == recount
    return recount


async def test_lot_counter_exists_from_creation(pg: tuple[httpx.AsyncClient, FastAPI]) -> None:
    _, app = pg
    assert await rows(
        app, "SELECT lot_id, occupied, version FROM lot_occupancy ORDER BY lot_id"
    ) == [
        ("anexo", 0, 0),
        ("principal", 0, 0),
    ]


async def test_ingest_and_availability(pg: tuple[httpx.AsyncClient, FastAPI]) -> None:
    client, app = pg
    batch = [
        event(1),
        event(2, student=REF_B),
        event(3, "exit", ago_min=5),
        event(4, student="stu_ffffffffffff"),
    ]
    r = await client.post(URL, json=batch, headers=INGEST)
    body = r.json()
    assert (r.status_code, body["accepted"], body["rejected"]) == (202, 3, 1)
    again = (await client.post(URL, json=batch, headers=INGEST)).json()
    assert (again["accepted"], again["duplicates"], again["rejected"]) == (0, 3, 1)
    out = (await client.get("/api/v1/lots/principal/availability")).json()
    assert (out["occupied"], out["version"]) == (1, 1)
    assert await occupancy_equals_recount(app) == {"principal": 1}
    page = (await client.get("/api/v1/operator/rejects", headers=OPERATOR)).json()
    assert [i["reason"] for i in page["items"]] == ["unknown_student", "unknown_student"]
    assert page["items"][0]["payload"]["student_ref"] == "stu_ffffffffffff"
    health = (await client.get("/api/v1/operator/ingestion/health", headers=OPERATOR)).json()
    assert health["rejects_by_reason"] == {"unknown_student": 2}


async def test_events_are_append_only(pg: tuple[httpx.AsyncClient, FastAPI]) -> None:
    client, app = pg
    await client.post(URL, json=[event(1)], headers=INGEST)
    for sql in (
        "UPDATE gate_events SET direction = 'exit'",
        "DELETE FROM gate_events",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with app.state.engine.begin() as conn:
                await conn.execute(text(sql))
    assert await rows(app, "SELECT count(*) FROM gate_events") == [(1,)]


async def test_concurrent_overlapping_batches_match_a_recount(
    pg: tuple[httpx.AsyncClient, FastAPI],
) -> None:
    client, app = pg
    students = [REF_A, REF_B, REF_C]
    n = iter(range(1, 10_000))
    batches = []
    for round_ in range(6):  # every batch touches the same three student-days
        batches.append(
            [
                event(
                    next(n),
                    "entry" if (i + round_) % 3 else "exit",
                    student=s,
                    ago_min=60 - 7 * round_ - i,
                )
                for i, s in enumerate(students * 2)
            ]
        )
    responses = await asyncio.gather(*(client.post(URL, json=b, headers=INGEST) for b in batches))
    assert [r.status_code for r in responses] == [202] * len(batches)
    assert sum(r.json()["accepted"] for r in responses) == sum(len(b) for b in batches)
    await occupancy_equals_recount(app)
    [(version,)] = await rows(app, "SELECT version FROM lot_occupancy WHERE lot_id = 'principal'")
    assert version == len(batches)


async def test_lock_timeout_is_429_and_the_retry_succeeds(migrated: str, client_for: Any) -> None:
    app = build_app(migrated, ingest_lock_timeout_ms=200)
    async with client_for(app) as client:
        await seed_registry(client)
        async with app.state.engine.connect() as holder:
            await holder.execute(
                text("SELECT 1 FROM lot_occupancy WHERE lot_id = 'principal' FOR UPDATE")
            )
            r = await client.post(URL, json=[event(1)], headers=INGEST)
            assert (r.status_code, r.json()["code"], r.headers["retry-after"]) == (
                429,
                "ingest-busy",
                "1",
            )
            await holder.rollback()
        assert await rows(app, "SELECT count(*) FROM gate_events") == [(0,)]
        assert (await client.post(URL, json=[event(1)], headers=INGEST)).status_code == 202
        assert "parking_ingest_throttled_total" in (await client.get("/metrics")).text


async def test_three_day_random_load(pg: tuple[httpx.AsyncClient, FastAPI]) -> None:
    client, app = pg
    rng = random.Random(42)
    refs = [f"stu_{i:012x}" for i in range(100, 400)]
    await client.post("/api/v1/admin/students/bulk", json={"student_refs": refs}, headers=ADMIN)
    local_midnight = NOW.astimezone(make_settings().tzinfo).replace(hour=0, minute=0, second=0)
    events: list[dict[str, Any]] = []
    counter = iter(range(1, 1_000_000))

    def add(direction: str, student: str, lot: str, at: Any) -> None:
        events.append(
            event(
                next(counter),
                direction,
                student,
                lot=lot,
                occurred_at=iso(at.astimezone(NOW.tzinfo)),
            )
        )

    for days_ago in (3, 2, 1):
        midnight = local_midnight - timedelta(days=days_ago)
        for ref in refs:
            for _ in range(rng.choice([0, 1, 1, 2])):
                lot = rng.choice(["principal", "anexo"])
                start = midnight + timedelta(minutes=rng.randint(360, 1000))
                add("entry", ref, lot, start)
                roll = rng.random()
                if roll < 0.8:
                    add("exit", ref, lot, start + timedelta(minutes=rng.randint(20, 300)))
                elif roll < 0.9:
                    add("entry", ref, lot, start + timedelta(minutes=rng.randint(1, 60)))
            if rng.random() < 0.05:
                add("exit", ref, "principal", midnight + timedelta(minutes=rng.randint(360, 1000)))
    sent = list(events) + rng.sample(events, len(events) // 10)  # resends
    rng.shuffle(sent)
    assert len(events) >= 1000

    accepted = duplicates = rejected = 0
    for start in range(0, len(sent), 250):
        r = await client.post(URL, json=sent[start : start + 250], headers=INGEST)
        assert r.status_code == 202
        body = r.json()
        accepted, duplicates, rejected = (
            accepted + body["accepted"],
            duplicates + body["duplicates"],
            rejected + body["rejected"],
        )
    assert (accepted, duplicates, rejected) == (len(events), len(sent) - len(events), 0)

    recount = await occupancy_equals_recount(app)
    assert sum(recount.values()) > 0
    assert await rows(app, "SELECT count(*) FROM gate_events") == [(len(events),)]
    unpaired = await rows(
        app,
        "SELECT count(*) FROM gate_events e WHERE (SELECT count(*) FROM stays s "
        "WHERE s.entry_event_id = e.event_id OR s.exit_event_id = e.event_id) <> 1",
    )
    assert unpaired == [(0,)]
    ids = {UUID(e["event_id"]) for e in events}
    stay_ids = await rows(app, "SELECT entry_event_id, exit_event_id FROM stays")
    used = [i for pair in stay_ids for i in pair if i is not None]
    assert len(used) == len(set(used)) == len(ids)
    out = (await client.get("/api/v1/availability")).json()
    assert {lot["lot_id"]: lot["occupied"] for lot in out["lots"]} == {
        "anexo": recount.get("anexo", 0),
        "principal": recount.get("principal", 0),
    }
