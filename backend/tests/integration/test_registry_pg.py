"""The registry API end to end on Postgres, on a schema built by the real migration."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from campus_parking.cli import alembic_config
from campus_parking.main import create_app
from tests.conftest import make_settings

ADMIN = {"X-API-Key": "a" * 32}
LOT = {
    "name": "Principal",
    "capacity_history": [
        {"valid_from": "2026-01-01", "capacity": 120},
        {"valid_from": "2026-06-01", "capacity": 150},
    ],
}


@pytest.fixture
def migrated(pg_database: str) -> str:
    command.upgrade(alembic_config(), "head")
    return pg_database


@pytest.fixture
async def pg_client(
    migrated: str, client_for: Callable[[Any], Any]
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    app = create_app(make_settings(database_url=migrated, admin_api_key=ADMIN["X-API-Key"]))
    async with client_for(app) as client:
        yield client, app.state.engine


async def test_put_lot_twice_is_idempotent(pg_client: tuple[httpx.AsyncClient, Any]) -> None:
    client, _ = pg_client
    first = await client.put("/api/v1/admin/lots/principal", json=LOT, headers=ADMIN)
    second = await client.put("/api/v1/admin/lots/principal", json=LOT, headers=ADMIN)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json() == second.json()
    assert (await client.get("/api/v1/lots")).json() == [first.json()]


async def test_gate_lifecycle_and_immutability(pg_client: tuple[httpx.AsyncClient, Any]) -> None:
    client, _ = pg_client
    await client.put("/api/v1/admin/lots/principal", json=LOT, headers=ADMIN)
    await client.put("/api/v1/admin/lots/anexo", json=LOT, headers=ADMIN)
    url = "/api/v1/admin/gates/principal-entrada"
    body = {"lot_id": "principal", "direction": "entry", "name": "Entrada"}
    assert (await client.put(url, json=body, headers=ADMIN)).status_code == 201
    assert (
        await client.put(url, json={**body, "name": "Entrada A"}, headers=ADMIN)
    ).status_code == 200
    for change in ({"direction": "exit"}, {"lot_id": "anexo"}):
        r = await client.put(url, json={**body, **change}, headers=ADMIN)
        assert (r.status_code, r.json()["code"]) == (409, "gate-immutable")
    ghost = {"lot_id": "ghost", "direction": "exit", "name": "x"}
    r = await client.put("/api/v1/admin/gates/ghost-saida", json=ghost, headers=ADMIN)
    assert (r.status_code, r.json()["code"]) == (404, "lot-not-found")
    gates = (await client.get("/api/v1/lots")).json()[1]["gates"]
    assert [(g["gate_id"], g["direction"], g["name"]) for g in gates] == [
        ("principal-entrada", "entry", "Entrada A")
    ]


async def test_bulk_students_with_duplicates(pg_client: tuple[httpx.AsyncClient, Any]) -> None:
    client, engine = pg_client
    # 990 distinct refs plus 10 repeats: exactly the 1000-item limit, duplicates counted once.
    refs = [f"stu_{i:012x}" for i in range(990)]
    url = "/api/v1/admin/students/bulk"
    first = await client.post(url, json={"student_refs": [*refs, *refs[:10]]}, headers=ADMIN)
    again = await client.post(
        url, json={"student_refs": [*refs[:5], "stu_ffffffffffff"]}, headers=ADMIN
    )
    assert first.json() == {"created": 990, "unchanged": 0}
    assert again.json() == {"created": 1, "unchanged": 5}
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM students")) == 991


@pytest.mark.parametrize("bad", ["bad", "stu_XYZ", "stu_0123456789AB", "stu_0123456789abc"])
async def test_student_ref_check_rejects_bad_value(
    pg_client: tuple[httpx.AsyncClient, Any], bad: str
) -> None:
    _, engine = pg_client
    with pytest.raises(IntegrityError, match="ck_students_student_ref_format"):
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO students VALUES (:ref, now(), now())"), {"ref": bad}
            )


async def test_database_checks_reject_bad_rows(pg_client: tuple[httpx.AsyncClient, Any]) -> None:
    client, engine = pg_client
    await client.put("/api/v1/admin/lots/principal", json=LOT, headers=ADMIN)
    with pytest.raises(IntegrityError, match="ck_lot_capacity_history_capacity_positive"):
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO lot_capacity_history VALUES ('principal', '2027-01-01', 0)")
            )
    with pytest.raises(IntegrityError, match="ck_gates_direction_valid"):
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO gates VALUES ('g-x', 'principal', 'up', 'x', now(), now())")
            )


async def test_concurrent_creates_of_the_same_lot_and_gate(
    pg_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """Simultaneous PUTs that both create the same row: one 201, one 200, never a 500."""
    client, engine = pg_client
    lots = await asyncio.gather(
        *(client.put("/api/v1/admin/lots/anexo", json=LOT, headers=ADMIN) for _ in range(4))
    )
    assert sorted(r.status_code for r in lots) == [200, 200, 200, 201]
    gate = {"lot_id": "anexo", "direction": "entry", "name": "Entrada"}
    gates = await asyncio.gather(
        *(
            client.put("/api/v1/admin/gates/anexo-entrada", json=gate, headers=ADMIN)
            for _ in range(4)
        )
    )
    assert sorted(r.status_code for r in gates) == [200, 200, 200, 201]
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM lot_capacity_history")) == 2
        assert await conn.scalar(text("SELECT count(*) FROM gates")) == 1
