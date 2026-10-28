"""Report routes on SQLite, with an attached in-memory database standing in for schema
`warehouse` (the marts dbt builds on Postgres)."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import insert

from campus_parking.core.clock import get_now
from campus_parking.core.db import Base
from campus_parking.core.problems import InvalidDateRange
from campus_parking.jobs.models import PipelineRun
from campus_parking.main import create_app
from campus_parking.registry.models import Lot
from campus_parking.reports import tables
from campus_parking.reports.service import ReportCache, matches, resolve_range
from tests.conftest import make_settings
from tests.helpers import NOW

TODAY = date(2026, 10, 28)  # local date of NOW
HOLIDAY = date(2026, 10, 12)  # a Monday
LOTS = ("anexo", "principal")


def daily_row(lot: str, day: date, entries: int, **extra: Any) -> dict[str, Any]:
    row = {
        "lot_id": lot,
        "service_date": day,
        "date_key": int(day.strftime("%Y%m%d")),
        "capacity": 100,
        "entries": entries,
        "exits": entries,
        "unique_students": entries - 1,
        "entries_ma7": float(entries),
        "avg_stay_min": 100.0,
        "median_stay_min": 90.0,
        "p90_stay_min": 200.0,
        "peak_occupied": entries // 2,
        "peak_slot": "08:00",
        "min_free": 100 - entries // 2,
        "minutes_full": 0.0,
        "stays": entries,
        "turnover": entries / 100,
        "closed_by_reconciliation": 1,
        "exit_only": 0,
        "missing_exit": 0,
    }
    return {**row, **extra}


@dataclass
class Env:
    client: httpx.AsyncClient
    app: FastAPI

    async def insert(self, table: Any, rows: list[dict[str, Any]]) -> None:
        async with self.app.state.engine.begin() as conn:
            await conn.execute(insert(table), rows)

    async def warehouse_run(self, finished_at: datetime, status: str = "succeeded") -> str:
        run_id = uuid4()
        await self.insert(
            PipelineRun.__table__,
            [
                {
                    "run_id": run_id,
                    "job": "warehouse",
                    "mode": "incremental",
                    "trigger": "schedule",
                    "service_date": TODAY,
                    "status": status,
                    "requested_at": finished_at - timedelta(minutes=1),
                    "finished_at": finished_at,
                }
            ],
        )
        return str(run_id)

    async def get(self, path: str, **params: Any) -> httpx.Response:
        return await self.client.get(f"/api/v1/reports/{path}", params=params)


async def _env(client_for: Callable[[FastAPI], Any], attach: bool) -> AsyncIterator[Env]:
    app = create_app(make_settings())
    app.dependency_overrides[get_now] = lambda: NOW
    async with client_for(app) as client:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if attach:
                await conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS warehouse")
                await conn.run_sync(tables.metadata.create_all)
        env = Env(client, app)
        await env.insert(
            Lot.__table__,
            [{"lot_id": lot, "name": lot, "created_at": NOW, "updated_at": NOW} for lot in LOTS],
        )
        yield env


@pytest.fixture
async def env(client_for: Callable[[FastAPI], Any]) -> AsyncIterator[Env]:
    async for e in _env(client_for, attach=True):
        await e.insert(
            tables.dim_date,
            [
                {
                    "date_key": int(d.strftime("%Y%m%d")),
                    "date_day": d,
                    "iso_day_of_week": d.isoweekday(),
                    "weekday_name": f"day{d.isoweekday()}",
                    "is_holiday": d == HOLIDAY,
                }
                for d in (date(2026, 9, 1) + timedelta(days=i) for i in range(60))
            ],
        )
        yield e


@pytest.fixture
async def bare(client_for: Callable[[FastAPI], Any]) -> AsyncIterator[Env]:
    async for e in _env(client_for, attach=False):
        yield e


def test_resolve_range_defaults_and_limits() -> None:
    assert resolve_range(None, None, TODAY) == (date(2026, 9, 29), TODAY)
    assert resolve_range(None, date(2026, 1, 30), TODAY) == (date(2026, 1, 1), date(2026, 1, 30))
    assert resolve_range(date(2025, 10, 28), None, TODAY)[0] == date(2025, 10, 28)  # 366 days
    for start, end in ((date(2025, 10, 27), None), (TODAY, date(2026, 10, 27))):
        with pytest.raises(InvalidDateRange):
            resolve_range(start, end, TODAY)


def test_if_none_match_parsing() -> None:
    assert matches('W/"a", "b"', '"a"')
    assert matches("*", '"x"')
    assert not matches(None, '"a"')
    assert not matches('"b"', '"a"')


def test_cache_clears_on_a_new_run_and_when_full() -> None:
    cache = ReportCache(max_entries=2)
    assert cache.get("r1", "k") is None
    cache.put("k", b"1")
    cache.put("k2", b"2")
    assert cache.get("r1", "k") == b"1"
    cache.put("k3", b"3")  # over the cap: cleared first
    assert cache.entries == {"k3": b"3"}
    assert cache.get("r2", "k3") is None


async def test_missing_marts_answer_503(bare: Env) -> None:
    for path in ("summary", "daily", "hourly", "weekday", "occupancy", "dwell", "quality"):
        r = await bare.get(path)
        assert r.status_code == 503, path
        assert r.json()["code"] == "warehouse-not-ready"
        assert r.headers["Retry-After"] == "60"


async def test_freshness_before_and_after_runs(env: Env) -> None:
    r = await env.get("freshness")
    assert r.status_code == 200
    assert r.json() == {
        "last_run_id": None,
        "finished_at": None,
        "mode": None,
        "status": None,
        "age_s": None,
    }
    run_id = await env.warehouse_run(NOW - timedelta(minutes=5))
    await env.warehouse_run(NOW - timedelta(minutes=1), status="failed")
    body = (await env.get("freshness")).json()
    assert (body["last_run_id"], body["mode"], body["status"]) == (run_id, "incremental", "failed")
    assert body["age_s"] == 300
    assert body["finished_at"] == "2026-10-28T13:55:00Z"


async def test_daily_defaults_filters_and_per_lot_series(env: Env) -> None:
    old = TODAY - timedelta(days=30)  # just outside the default 30 days
    await env.insert(
        tables.mart_lot_daily,
        [daily_row(lot, d, 10) for lot in LOTS for d in (old, TODAY - timedelta(days=29), TODAY)],
    )
    r = await env.get("daily")
    assert r.status_code == 200
    rows = r.json()
    assert [(x["lot_id"], x["service_date"]) for x in rows] == [
        ("anexo", "2026-09-29"),
        ("anexo", "2026-10-28"),
        ("principal", "2026-09-29"),
        ("principal", "2026-10-28"),
    ]
    assert rows[0]["peak_slot"] == "08:00" and rows[0]["turnover"] == 0.1
    one = (
        await env.get("daily", lot_id="anexo", **{"from": old.isoformat(), "to": old.isoformat()})
    ).json()
    assert [(x["lot_id"], x["service_date"]) for x in one] == [("anexo", old.isoformat())]


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("daily", {"from": "2026-10-10", "to": "2026-10-01"}),
        ("summary", {"from": "2025-10-27", "to": "2026-10-28"}),
        ("hourly", {"from": "2026-09-27", "to": "2026-10-28"}),
    ],
)
async def test_invalid_ranges_are_422(env: Env, path: str, params: dict[str, str]) -> None:
    r = await env.get(path, **params)
    assert r.status_code == 422
    assert r.json()["code"] == "invalid-date-range"


async def test_unknown_lot_is_404_and_bad_input_422(env: Env) -> None:
    r = await env.get("daily", lot_id="nowhere")
    assert (r.status_code, r.json()["code"]) == (404, "lot-not-found")
    assert (await env.get("daily", lot_id="Not A Slug")).status_code == 422
    assert (await env.get("occupancy", date="yesterday")).status_code == 422


async def test_summary_aggregates_each_lot_on_its_own(env: Env) -> None:
    days = [TODAY - timedelta(days=1), TODAY]
    await env.insert(
        tables.mart_lot_daily,
        [daily_row("anexo", days[0], 10, p90_stay_min=100.0), daily_row("anexo", days[1], 30)]
        + [daily_row("principal", d, 4) for d in days],
    )
    rows = (await env.get("summary", **{"from": days[0].isoformat()})).json()
    anexo, principal = rows
    assert anexo == {
        "lot_id": "anexo",
        "from_date": days[0].isoformat(),
        "to_date": TODAY.isoformat(),
        "days": 2,
        "entries": 40,
        "exits": 40,
        "avg_daily_entries": 20.0,
        "avg_daily_unique_students": 19.0,
        "peak_occupied": 15,
        "min_free": 85,
        "minutes_full": 0.0,
        "avg_stay_min": 100.0,
        "p90_stay_min": 150.0,  # mean of the daily p90s
        "avg_turnover": 0.2,
        "closed_by_reconciliation": 2,
        "exit_only": 0,
        "missing_exit": 0,
    }
    assert (principal["lot_id"], principal["entries"]) == ("principal", 8)
    only = (await env.get("summary", lot_id="principal")).json()
    assert [x["lot_id"] for x in only] == ["principal"]


async def test_weekday_excludes_holidays(env: Env) -> None:
    mondays = [date(2026, 10, 5), HOLIDAY, date(2026, 10, 19)]
    await env.insert(
        tables.mart_lot_daily,
        [daily_row("anexo", d, n) for d, n in zip(mondays, (10, 999, 30), strict=True)]
        + [daily_row("anexo", date(2026, 10, 20), 7)],
    )
    rows = (await env.get("weekday", lot_id="anexo")).json()
    assert [(x["iso_day_of_week"], x["days"], x["avg_entries"]) for x in rows] == [
        (1, 2, 20.0),
        (2, 1, 7.0),
    ]
    assert rows[0]["weekday_name"] == "day1"


async def test_hourly_occupancy_dwell_and_quality(env: Env) -> None:
    await env.insert(
        tables.mart_lot_hourly,
        [
            {
                "lot_id": lot,
                "service_date": TODAY,
                "date_key": 20261028,
                "hour": h,
                "capacity": 100,
                "entries": h,
                "exits": 0,
                "avg_free": 99.5,
                "avg_occupied": 0.5,
                "max_occupied": 1,
            }
            for lot in LOTS
            for h in (8, 7)
        ],
    )
    hourly = (await env.get("hourly", lot_id="anexo")).json()
    assert [x["hour"] for x in hourly] == [7, 8]
    assert hourly[0]["avg_free"] == 99.5

    start = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
    await env.insert(
        tables.fct_occupancy_snapshot,
        [
            {
                "lot_id": "anexo",
                "service_date": d,
                "slot_key": k,
                "slot_start": start + timedelta(minutes=15 * k),
                "occupied_end": k,
                "occupied_max": k,
                "occupied_avg": k / 2,
                "capacity": 100,
                "free_end": 100 - k,
                "free_min": 100 - k,
                "seconds_full": 0,
            }
            for d in (TODAY - timedelta(days=1), TODAY)
            for k in (1, 0)
        ],
    )
    slots = (await env.get("occupancy")).json()  # default: today
    assert [(x["service_date"], x["slot_key"]) for x in slots] == [
        ("2026-10-28", 0),
        ("2026-10-28", 1),
    ]
    assert slots[1]["slot_start"] == "2026-10-28T03:15:00Z"
    assert len((await env.get("occupancy", date="2026-10-27", lot_id="anexo")).json()) == 2
    assert (await env.get("occupancy", lot_id="principal")).json() == []

    buckets = [("<30m", 1), ("30-60m", 2)]
    await env.insert(
        tables.mart_lot_dwell_daily,
        [
            {
                "lot_id": "anexo",
                "service_date": d,
                "date_key": 0,
                "duration_bucket": b,
                "bucket_order": o,
                "stays": o,
            }
            for d in (TODAY - timedelta(days=1), TODAY)
            for b, o in buckets
        ],
    )
    dwell = (await env.get("dwell")).json()
    assert [(x["duration_bucket"], x["stays"]) for x in dwell] == [("<30m", 2), ("30-60m", 4)]

    zero = {f"rejects_{r}": 0 for r in tables.REJECT_REASONS}
    await env.insert(
        tables.mart_data_quality_daily,
        [
            {
                "service_date": TODAY,
                "lot_id": lot,
                "date_key": 20261028,
                **zero,
                "rejects_total": n,
                "rejects_unknown_gate": n,
                "closed_by_reconciliation": 0,
                "exit_only": 0,
                "missing_exit": 0,
            }
            for lot, n in (("-1", 2), ("anexo", 1))
        ],
    )
    quality = (await env.get("quality")).json()
    assert [(x["lot_id"], x["rejects_unknown_gate"]) for x in quality] == [("-1", 2), ("anexo", 1)]


async def test_etag_304_and_invalidation_on_a_new_warehouse_run(env: Env) -> None:
    await env.warehouse_run(NOW - timedelta(minutes=10))
    await env.insert(tables.mart_lot_daily, [daily_row("anexo", TODAY, 5)])
    first = await env.get("daily")
    assert first.headers["Cache-Control"] == "public, max-age=60"
    tag = first.headers["ETag"]
    again = await env.client.get("/api/v1/reports/daily", headers={"If-None-Match": tag})
    assert (again.status_code, again.content, again.headers["ETag"]) == (304, b"", tag)
    other = await env.get("daily", lot_id="anexo")
    assert other.headers["ETag"] != tag  # the query is part of the tag

    # New mart rows stay invisible until a warehouse run succeeds: the cache is per run.
    await env.insert(tables.mart_lot_daily, [daily_row("principal", TODAY, 6)])
    assert (await env.get("daily")).json() == first.json()
    await env.warehouse_run(NOW - timedelta(minutes=1), status="failed")
    assert (await env.get("daily")).headers["ETag"] == tag
    await env.warehouse_run(NOW)
    fresh = await env.get("daily")
    assert fresh.headers["ETag"] != tag
    assert [x["lot_id"] for x in fresh.json()] == ["anexo", "principal"]
    stale = await env.client.get("/api/v1/reports/daily", headers={"If-None-Match": tag})
    assert stale.status_code == 200
