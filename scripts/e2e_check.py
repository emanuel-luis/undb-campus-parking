"""End-to-end check for the generator CI job: what the backend holds after bootstrap and backfill.

Run with the backend environment (it has asyncpg):

    uv run --project backend python scripts/e2e_check.py first.log rerun.log

The two arguments are the stderr of `turnstile-gen backfill` for the first run and the rerun. This
is a test, so it reads Postgres directly; the generator itself never does. Reads
PARKING_DATABASE_URL and GENERATOR_STUDENTS (default 10000). Exits non-zero on the first broken
expectation.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]

INJECTED = {"unknown_student", "unknown_gate"}  # the only reject reasons the generator injects
LOTS = {
    "principal": ("Estacionamento Principal", [200]),
    "anexo": ("Estacionamento Anexo", [120, 150]),
}
GATES = {"principal-entrada", "principal-saida", "anexo-entrada", "anexo-saida"}


def summary_of(path: str) -> dict[str, Any]:
    """The 'backfill finished' log line of a run."""
    for line in reversed(Path(path).read_text(encoding="utf-8").splitlines()):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("message") == "backfill finished":
            return record
    raise SystemExit(f"{path}: no 'backfill finished' line (did the backfill fail?)")


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"E2E CHECK FAILED: {message}")
    print(f"ok  {message}")


async def main(first_log: str, rerun_log: str) -> None:
    first, rerun = summary_of(first_log), summary_of(rerun_log)
    dsn = os.environ["PARKING_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    students = int(os.environ.get("GENERATOR_STUDENTS", "10000"))
    conn = await asyncpg.connect(dsn)
    try:
        lots = {r["lot_id"]: r["name"] for r in await conn.fetch("SELECT lot_id, name FROM lots")}
        expect(lots == {k: v[0] for k, v in LOTS.items()}, f"lots are exactly {sorted(LOTS)}")
        for lot_id, (_, capacities) in LOTS.items():
            rows = await conn.fetch(
                "SELECT capacity FROM lot_capacity_history WHERE lot_id = $1 ORDER BY valid_from",
                lot_id,
            )
            expect([r["capacity"] for r in rows] == capacities, f"{lot_id} capacity {capacities}")
        gates = {r["gate_id"] for r in await conn.fetch("SELECT gate_id FROM gates")}
        expect(gates == GATES, "the four gates exist")
        count = await conn.fetchval("SELECT count(*) FROM students")
        expect(count == students, f"{students} students registered (found {count})")

        events = await conn.fetchval("SELECT count(*) FROM gate_events")
        expect(
            events == first["accepted"],
            f"gate_events ({events}) = accepted reported ({first['accepted']})",
        )
        # A 7-day window is at least ~3k events even when it holds a holiday and a quiet
        # semester week, so 1500 leaves margin yet still catches an empty or broken backfill.
        expect(events > 1500, f"the backfill produced a realistic volume ({events})")
        expect(
            rerun["accepted"] == 0
            and rerun["duplicates"] == first["accepted"] + first["duplicates"],
            "the rerun reports only duplicates",
        )
        expect(rerun["rejected"] == first["rejected"], "the rerun rejects exactly the same items")

        reasons = set(first["rejects_by_reason"]) | set(rerun["rejects_by_reason"])
        stored = {
            r["reason"] for r in await conn.fetch("SELECT DISTINCT reason FROM ingest_rejects")
        }
        expect(not {"too_old", "in_future"} & (reasons | stored), "no too_old or in_future rejects")
        expect(
            (reasons | stored) <= INJECTED,
            f"rejects are only injected kinds ({sorted(reasons | stored)})",
        )

        rows = await conn.fetch(
            """
            SELECT o.lot_id, o.occupied,
                   (SELECT count(*) FROM stays s WHERE s.lot_id = o.lot_id AND s.status = 'open') AS open
            FROM lot_occupancy o ORDER BY o.lot_id
            """
        )
        expect(len(rows) == len(LOTS), "one lot_occupancy row per lot")
        for r in rows:
            expect(
                r["occupied"] == r["open"],
                f"lot_occupancy.occupied = recount of open stays for {r['lot_id']} ({r['occupied']})",
            )
        by_status = await conn.fetch(
            "SELECT status, count(*) AS n FROM stays GROUP BY status ORDER BY status"
        )
        print("stays:", {r["status"]: r["n"] for r in by_status})
    finally:
        await conn.close()
    print(
        f"E2E OK: {events} events, {first['rejected']} injected rejects, "
        f"{first['duplicates']} duplicates, rerun accepted {rerun['accepted']}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
