from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from campus_parking.core.clock import get_now
from campus_parking.core.db import Base
from campus_parking.main import create_app
from tests.conftest import make_settings

ADMIN = {"X-API-Key": "a" * 32}
# 2026-10-28 02:00 UTC is still 2026-10-27 23:00 in America/Fortaleza (UTC-3).
NOW = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)
REF_A = "stu_0123456789ab"
REF_B = "stu_ba9876543210"


@pytest.fixture
def app() -> FastAPI:
    app = create_app(
        make_settings(
            ingest_api_key="i" * 32, admin_api_key=ADMIN["X-API-Key"], operator_api_key="o" * 32
        )
    )
    app.dependency_overrides[get_now] = lambda: NOW
    return app


@pytest.fixture
async def client(
    app: FastAPI, client_for: Callable[[FastAPI], Any]
) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(app) as c:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield c


def lot_body(*periods: tuple[str, int], name: str = "Principal") -> dict[str, Any]:
    return {
        "name": name,
        "capacity_history": [{"valid_from": d, "capacity": c} for d, c in periods],
    }


async def put_lot(
    client: httpx.AsyncClient, lot_id: str = "principal", **kw: Any
) -> httpx.Response:
    body = lot_body(("2026-01-01", 200), **kw)
    return await client.put(f"/api/v1/admin/lots/{lot_id}", json=body, headers=ADMIN)


async def test_list_empty(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/lots")
    assert r.status_code == 200
    assert r.json() == []


async def test_put_lot_201_then_200_identical(client: httpx.AsyncClient) -> None:
    body = lot_body(("2026-06-01", 150), ("2026-01-01", 120))
    first = await client.put("/api/v1/admin/lots/principal", json=body, headers=ADMIN)
    second = await client.put("/api/v1/admin/lots/principal", json=body, headers=ADMIN)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json() == second.json()
    out = first.json()
    assert out["capacity"] == 150
    assert out["capacity_history"] == [
        {"valid_from": "2026-01-01", "capacity": 120},
        {"valid_from": "2026-06-01", "capacity": 150},
    ]
    assert out["gates"] == []


async def test_put_lot_replaces_name_and_whole_history(client: httpx.AsyncClient) -> None:
    await put_lot(client)
    body = lot_body(("2026-09-01", 90), name="Principal 2")
    r = await client.put("/api/v1/admin/lots/principal", json=body, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["name"] == "Principal 2"
    assert r.json()["capacity_history"] == [{"valid_from": "2026-09-01", "capacity": 90}]
    assert (await client.get("/api/v1/lots")).json() == [r.json()]


async def test_current_capacity_uses_local_today(client: httpx.AsyncClient) -> None:
    # UTC date is 2026-10-28 but the service date (Fortaleza) is still 2026-10-27.
    body = lot_body(("2026-10-27", 100), ("2026-10-28", 200))
    r = await client.put("/api/v1/admin/lots/principal", json=body, headers=ADMIN)
    assert r.json()["capacity"] == 100


async def test_all_future_history_has_null_capacity(client: httpx.AsyncClient) -> None:
    body = lot_body(("2026-11-01", 100), ("2027-01-01", 300))
    r = await client.put("/api/v1/admin/lots/principal", json=body, headers=ADMIN)
    assert r.json()["capacity"] is None
    assert len(r.json()["capacity_history"]) == 2


async def test_list_lots_sorted_with_sorted_gates(client: httpx.AsyncClient) -> None:
    await put_lot(client, "zeta")
    await put_lot(client, "alfa")
    for gate_id, lot, direction in (
        ("alfa-saida", "alfa", "exit"),
        ("alfa-entrada", "alfa", "entry"),
    ):
        r = await client.put(
            f"/api/v1/admin/gates/{gate_id}",
            json={"lot_id": lot, "direction": direction, "name": gate_id},
            headers=ADMIN,
        )
        assert r.status_code == 201
    lots = (await client.get("/api/v1/lots")).json()
    assert [lot["lot_id"] for lot in lots] == ["alfa", "zeta"]
    assert [g["gate_id"] for g in lots[0]["gates"]] == ["alfa-entrada", "alfa-saida"]
    assert lots[1]["gates"] == []
    assert lots[0]["gates"][0] == {
        "gate_id": "alfa-entrada",
        "lot_id": "alfa",
        "direction": "entry",
        "name": "alfa-entrada",
    }


@pytest.mark.parametrize(
    "path",
    ["/api/v1/admin/lots/A", "/api/v1/admin/lots/x", "/api/v1/admin/lots/has_underscore"],
)
async def test_put_lot_invalid_slug_is_422_problem(client: httpx.AsyncClient, path: str) -> None:
    r = await client.put(path, json=lot_body(("2026-01-01", 1)), headers=ADMIN)
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "validation-error"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "capacity_history": [{"valid_from": "2026-01-01", "capacity": 1}]},
        {"name": "x" * 81, "capacity_history": [{"valid_from": "2026-01-01", "capacity": 1}]},
        {"name": "ok", "capacity_history": []},
        {"name": "ok", "capacity_history": [{"valid_from": "2026-01-01", "capacity": 0}]},
        {
            "name": "ok",
            "capacity_history": [
                {"valid_from": "2026-01-01", "capacity": 1},
                {"valid_from": "2026-01-01", "capacity": 2},
            ],
        },
        {
            "name": "ok",
            "capacity_history": [
                {"valid_from": f"2026-01-{d:02d}", "capacity": 1} for d in range(1, 29)
            ]
            * 2,
        },
        {"name": "ok", "capacity_history": [{"valid_from": "2026-01-01", "capacity": 1}], "x": 1},
    ],
)
async def test_put_lot_body_validation(client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    r = await client.put("/api/v1/admin/lots/principal", json=body, headers=ADMIN)
    assert r.status_code == 422
    assert r.json()["code"] == "validation-error"


async def test_put_gate_create_rename_idempotent(client: httpx.AsyncClient) -> None:
    await put_lot(client)
    body = {"lot_id": "principal", "direction": "entry", "name": "Entrada"}
    url = "/api/v1/admin/gates/principal-entrada"
    first = await client.put(url, json=body, headers=ADMIN)
    again = await client.put(url, json=body, headers=ADMIN)
    renamed = await client.put(url, json={**body, "name": "Entrada A"}, headers=ADMIN)
    assert (first.status_code, again.status_code, renamed.status_code) == (201, 200, 200)
    assert again.json() == first.json()
    assert renamed.json()["name"] == "Entrada A"
    lots = (await client.get("/api/v1/lots")).json()
    assert lots[0]["gates"][0]["name"] == "Entrada A"


async def test_put_gate_unknown_lot_is_404_problem(client: httpx.AsyncClient) -> None:
    r = await client.put(
        "/api/v1/admin/gates/g-1",
        json={"lot_id": "ghost", "direction": "exit", "name": "x"},
        headers=ADMIN,
    )
    assert r.status_code == 404
    assert r.json()["code"] == "lot-not-found"
    assert r.json()["type"] == "/problems/lot-not-found"


@pytest.mark.parametrize("change", [{"lot_id": "other"}, {"direction": "exit"}])
async def test_put_gate_immutable_is_409(client: httpx.AsyncClient, change: dict[str, str]) -> None:
    await put_lot(client)
    await put_lot(client, "other")
    url = "/api/v1/admin/gates/principal-entrada"
    body = {"lot_id": "principal", "direction": "entry", "name": "Entrada"}
    assert (await client.put(url, json=body, headers=ADMIN)).status_code == 201
    r = await client.put(url, json={**body, **change}, headers=ADMIN)
    assert r.status_code == 409
    assert r.json()["code"] == "gate-immutable"
    stored = (await client.get("/api/v1/lots")).json()
    assert [g["direction"] for lot in stored for g in lot["gates"]] == ["entry"]


async def test_put_gate_validation(client: httpx.AsyncClient) -> None:
    await put_lot(client)
    bad_dir = {"lot_id": "principal", "direction": "sideways", "name": "x"}
    assert (
        await client.put("/api/v1/admin/gates/g-1", json=bad_dir, headers=ADMIN)
    ).status_code == 422
    ok = {"lot_id": "principal", "direction": "entry", "name": "x"}
    assert (
        await client.put("/api/v1/admin/gates/BAD_ID", json=ok, headers=ADMIN)
    ).status_code == 422


async def test_bulk_students_counts_and_duplicates(client: httpx.AsyncClient) -> None:
    url = "/api/v1/admin/students/bulk"
    first = await client.post(url, json={"student_refs": [REF_A, REF_A, REF_B]}, headers=ADMIN)
    again = await client.post(
        url, json={"student_refs": [REF_B, "stu_ffffffffffff"]}, headers=ADMIN
    )
    assert first.status_code == 200
    assert first.json() == {"created": 2, "unchanged": 0}
    assert again.json() == {"created": 1, "unchanged": 1}


@pytest.mark.parametrize(
    "refs",
    [[], ["stu_XYZ"], ["stu_0123456789AB"], ["x"], [REF_A] * 1001, [123]],
)
async def test_bulk_students_validation(client: httpx.AsyncClient, refs: list[Any]) -> None:
    r = await client.post("/api/v1/admin/students/bulk", json={"student_refs": refs}, headers=ADMIN)
    assert r.status_code == 422


async def test_bulk_students_thousand_distinct(client: httpx.AsyncClient) -> None:
    refs = [f"stu_{i:012x}" for i in range(1000)]
    r = await client.post("/api/v1/admin/students/bulk", json={"student_refs": refs}, headers=ADMIN)
    assert r.json() == {"created": 1000, "unchanged": 0}


ADMIN_CALLS = [
    ("PUT", "/api/v1/admin/lots/principal", lot_body(("2026-01-01", 1))),
    ("PUT", "/api/v1/admin/gates/g-1", {"lot_id": "principal", "direction": "entry", "name": "x"}),
    ("POST", "/api/v1/admin/students/bulk", {"student_refs": [REF_A]}),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_CALLS)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-API-Key": "w" * 32},
        {"X-API-Key": "i" * 32},  # ingest key
        {"X-API-Key": "o" * 32},  # operator key
    ],
    ids=["no-key", "wrong-key", "ingest-key", "operator-key"],
)
async def test_admin_auth_matrix(
    client: httpx.AsyncClient, method: str, path: str, body: dict[str, Any], headers: dict[str, str]
) -> None:
    r = await client.request(method, path, json=body, headers=headers)
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"
    assert r.headers["www-authenticate"] == "ApiKey"


async def test_public_lots_needs_no_key(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/lots")).status_code == 200


async def test_openapi_documents_registry(client: httpx.AsyncClient) -> None:
    doc = (await client.get("/openapi.json")).json()
    paths = doc["paths"]
    assert paths["/api/v1/lots"]["get"]["tags"] == ["lots"]
    for path, method in (
        ("/api/v1/admin/lots/{lot_id}", "put"),
        ("/api/v1/admin/gates/{gate_id}", "put"),
        ("/api/v1/admin/students/bulk", "post"),
    ):
        op = paths[path][method]
        assert op["tags"] == ["admin"]
        assert "401" in op["responses"]
    assert {"201", "200"} <= set(paths["/api/v1/admin/lots/{lot_id}"]["put"]["responses"])
    assert {"404", "409"} <= set(paths["/api/v1/admin/gates/{gate_id}"]["put"]["responses"])
    assert {t["name"] for t in doc["tags"]} >= {"lots", "admin"}
