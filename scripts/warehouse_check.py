"""End-to-end check of the warehouse and the report routes (CI job generator-e2e).

    uv run --project backend python scripts/warehouse_check.py snapshot marts.json
    uv run --project backend python scripts/warehouse_check.py verify marts.json

`snapshot` stores a checksum of every mart (days before today, local time: today's rows grow
with the clock). `verify`, after another dbt run, checks the checksums did not change, then
calls each report route and compares it with Postgres. Reads PARKING_DATABASE_URL and
PARKING_TIMEZONE; the API is at REPORTS_API_URL (default http://127.0.0.1:8000). This is a
test, so it reads Postgres directly. Exits non-zero on the first broken expectation.
"""

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import httpx

MARTS = ("mart_lot_daily", "mart_lot_hourly", "mart_lot_dwell_daily", "mart_data_quality_daily")
TZ = os.environ.get("PARKING_TIMEZONE", "America/Fortaleza")
API = os.environ.get("REPORTS_API_URL", "http://127.0.0.1:8000") + "/api/v1/reports"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"WAREHOUSE CHECK FAILED: {message}")
    print(f"ok  {message}")


async def connect() -> Any:
    dsn = os.environ["PARKING_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


async def checksums(conn: Any) -> dict[str, str]:
    out = {}
    for mart in MARTS:
        out[mart] = await conn.fetchval(
            f"""
            SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))
            FROM warehouse.{mart} AS t
            WHERE service_date < (now() AT TIME ZONE $1)::date
            """,
            TZ,
        )
    return out


async def snapshot(path: str) -> None:
    conn = await connect()
    try:
        sums = await checksums(conn)
        for mart in MARTS:
            rows = await conn.fetchval(f"SELECT count(*) FROM warehouse.{mart}")
            expect(rows > 0, f"{mart} has rows ({rows})")
    finally:
        await conn.close()
    Path(path).write_text(json.dumps(sums, indent=2), encoding="utf-8")


async def verify(path: str) -> None:
    before = json.loads(Path(path).read_text(encoding="utf-8"))
    conn = await connect()
    try:
        after = await checksums(conn)
        for mart in MARTS:
            expect(after[mart] == before[mart], f"{mart} unchanged by the incremental build")
        first, today = await conn.fetchrow(
            "SELECT min(service_date), (now() AT TIME ZONE $1)::date FROM gate_events", TZ
        )
        await check_reports(conn, first, today)
        rows = await conn.fetch(
            """
            SELECT o.lot_id, o.occupied,
                   (SELECT count(*) FROM stays s WHERE s.lot_id = o.lot_id AND s.status = 'open') AS open
            FROM lot_occupancy o
            """
        )
        for r in rows:
            expect(r["occupied"] == r["open"], f"occupied = recount of open stays for {r['lot_id']}")
    finally:
        await conn.close()
    print("WAREHOUSE OK")


async def check_reports(conn: Any, first: date, today: date) -> None:
    window = {"from": first.isoformat(), "to": today.isoformat()}
    async with httpx.AsyncClient(base_url=API, timeout=30) as api:
        fresh = (await api.get("/freshness")).json()
        expect(fresh["last_run_id"] is not None, f"freshness names a run ({fresh['mode']})")

        r = await api.get("/daily", params=window)
        expect(r.status_code == 200, "GET /daily answers 200")
        daily = r.json()
        accepted = await conn.fetchval(
            "SELECT count(*) FROM gate_events WHERE direction = 'entry' "
            "AND service_date BETWEEN $1 AND $2",
            first,
            today,
        )
        entries = sum(row["entries"] for row in daily)
        expect(entries == accepted, f"sum of daily entries ({entries}) = accepted entries")
        expect(all(row["minutes_full"] >= 0 for row in daily), "minutes_full >= 0")
        expect(
            all(0 <= row["min_free"] <= row["capacity"] for row in daily),
            "0 <= min_free <= capacity on every lot-day",
        )
        lots = {row["lot_id"] for row in daily}
        expect(len(lots) == 2, f"one series per lot ({sorted(lots)})")

        tag = r.headers["ETag"]
        again = await api.get("/daily", params=window, headers={"If-None-Match": tag})
        expect(again.status_code == 304, "If-None-Match with the ETag answers 304")

        last = max(row["service_date"] for row in daily if row["entries"] > 0)
        hourly = (await api.get("/hourly", params={"from": last, "to": last})).json()
        by_lot = {lot: sum(h["entries"] for h in hourly if h["lot_id"] == lot) for lot in lots}
        want = {row["lot_id"]: row["entries"] for row in daily if row["service_date"] == last}
        expect(by_lot == want, f"hourly entries add up to the daily ones on {last}")

        slots = (await api.get("/occupancy", params={"date": last})).json()
        expect(len(slots) == 2 * 96, f"96 slots per lot on {last}")
        expect(
            all(0 <= s["free_end"] <= s["capacity"] and s["occupied_end"] >= 0 for s in slots),
            "free never above capacity, occupancy never negative",
        )

        for route in ("summary", "weekday", "dwell", "quality"):
            res = await api.get(f"/{route}", params=window)
            expect(res.status_code == 200 and len(res.json()) > 0, f"GET /{route} has rows")
        dwell = (await api.get("/dwell", params=window)).json()
        closed = await conn.fetchval(
            "SELECT count(*) FROM stays WHERE status = 'closed' AND service_date BETWEEN $1 AND $2",
            first,
            today,
        )
        expect(sum(b["stays"] for b in dwell) == closed, "dwell buckets add up to closed stays")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("snapshot", "verify"):
        raise SystemExit(__doc__)
    asyncio.run(snapshot(sys.argv[2]) if sys.argv[1] == "snapshot" else verify(sys.argv[2]))
