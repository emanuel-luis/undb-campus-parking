import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError, OperationalError

from campus_parking.core.clock import get_now
from campus_parking.core.db import Base
from campus_parking.ingestion.service import IngestionService
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


@pytest.fixture
def app() -> FastAPI:
    app = create_app(
        make_settings(
            ingest_api_key=INGEST["X-API-Key"],
            admin_api_key=ADMIN["X-API-Key"],
            operator_api_key=OPERATOR["X-API-Key"],
            ingest_max_batch=5,
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
        await seed_registry(c)
        yield c


async def post(client: httpx.AsyncClient, body: Any, headers: dict[str, str] = INGEST) -> Any:
    return await client.post(URL, json=body, headers=headers)


async def lot(client: httpx.AsyncClient, lot_id: str = "principal") -> dict[str, Any]:
    r = await client.get(f"/api/v1/lots/{lot_id}/availability")
    assert r.status_code == 200
    out: dict[str, Any] = r.json()
    return out


async def test_new_lot_starts_with_a_zero_counter(client: httpx.AsyncClient) -> None:
    body = await lot(client)
    assert body["occupied"] == 0
    assert body["version"] == 0
    assert body["last_event_at"] is None
    snapshot = (await client.get("/api/v1/availability")).json()
    assert [x["lot_id"] for x in snapshot["lots"]] == ["anexo", "principal"]
    assert snapshot["generated_at"].startswith("2026-10-28T14:00:00")


async def test_accepted_events_move_availability(client: httpx.AsyncClient) -> None:
    r = await post(client, [event(1), event(2, student=REF_B)])
    assert r.status_code == 202
    body = r.json()
    assert (body["received"], body["accepted"], body["duplicates"], body["rejected"]) == (
        2,
        2,
        0,
        0,
    )
    assert body["rejects"] == []
    out = await lot(client)
    assert (out["capacity"], out["occupied"], out["free"], out["occupancy_pct"]) == (3, 2, 1, 66.7)
    assert (out["over_capacity"], out["version"]) == (False, 1)
    assert out["last_event_at"].startswith("2026-10-28T13:50:00")
    assert (await lot(client, "anexo"))["occupied"] == 0


async def test_exit_frees_the_space_and_resend_is_a_duplicate(client: httpx.AsyncClient) -> None:
    batch = [event(1), event(2, "exit", ago_min=5)]
    assert (await post(client, batch)).json()["accepted"] == 2
    assert (await lot(client))["occupied"] == 0
    again = (await post(client, batch)).json()
    assert (again["accepted"], again["duplicates"], again["rejected"]) == (0, 2, 0)
    out = await lot(client)
    assert (out["occupied"], out["version"]) == (0, 1)


async def test_over_capacity_is_exact_but_free_is_clamped(client: httpx.AsyncClient) -> None:
    refs = ["stu_00000000000d", "stu_00000000000e"]
    await client.post("/api/v1/admin/students/bulk", json={"student_refs": refs}, headers=ADMIN)
    students = [REF_A, REF_B, REF_C, *refs]
    await post(client, [event(i, student=s) for i, s in enumerate(students[:5], 1)])
    out = await lot(client)
    assert (out["occupied"], out["free"], out["over_capacity"], out["occupancy_pct"]) == (
        5,
        0,
        True,
        166.7,
    )


async def test_every_reject_reason(client: httpx.AsyncClient) -> None:
    items: list[Any] = [
        event(1, student="stu_ffffffffffff"),  # 0 unknown_student
        event(2, gate_id="ghost-gate"),  # 1 unknown_gate
        event(3, lot="anexo", gate_id="principal-entrada"),  # 2 lot_mismatch
        event(4, "exit", gate_id="principal-entrada"),  # 3 direction_mismatch
        event(5, ago_min=-6),  # 4 in_future (more than 300 s ahead)
        event(6, ago_min=120 * 24 * 60 + 1),  # 5 too_old
        {"event_id": "not-a-uuid", "gate_id": "principal-entrada"},  # 6 schema_invalid
    ]
    first, second = items[:5], items[5:]
    r1 = (await post(client, first)).json()
    r2 = (await post(client, second)).json()
    assert (r1["accepted"], r1["rejected"], r2["rejected"]) == (0, 5, 2)
    reasons = [x["reason"] for x in r1["rejects"] + r2["rejects"]]
    assert reasons == [
        "unknown_student",
        "unknown_gate",
        "lot_mismatch",
        "direction_mismatch",
        "in_future",
        "too_old",
        "schema_invalid",
    ]
    assert [x["index"] for x in r1["rejects"]] == [0, 1, 2, 3, 4]
    assert r2["rejects"][1]["event_id"] is None
    assert (await lot(client))["version"] == 0


async def test_rejects_never_fail_the_batch(client: httpx.AsyncClient) -> None:
    r = await post(
        client, [event(1), event(2, student="stu_ffffffffffff"), event(3, student=REF_B)]
    )
    body = r.json()
    assert (r.status_code, body["accepted"], body["rejected"]) == (202, 2, 1)
    assert (await lot(client))["occupied"] == 2


async def test_event_id_conflict(client: httpx.AsyncClient) -> None:
    assert (await post(client, [event(1)])).json()["accepted"] == 1
    clash = event(1, student=REF_B)
    body = (await post(client, [clash])).json()
    assert (body["accepted"], body["duplicates"], body["rejected"]) == (0, 0, 1)
    assert body["rejects"][0]["reason"] == "event_id_conflict"
    # Within one batch: the first one wins, an identical copy is a duplicate, a different one
    # is a conflict with what was stored.
    fresh = event(7)
    body = (await post(client, [fresh, fresh, event(7, student=REF_C)])).json()
    assert (body["accepted"], body["duplicates"], body["rejected"]) == (1, 1, 1)
    assert body["rejects"][0]["index"] == 2
    # The same instant written with another offset hashes the same: a duplicate, not a conflict.
    shifted = event(7, occurred_at="2026-10-28T10:50:00.000-03:00")
    assert (await post(client, [shifted])).json()["duplicates"] == 1


@pytest.mark.parametrize(
    "body",
    [{"event": 1}, [], [event(i) for i in range(1, 7)], "text", None],
)
async def test_whole_batch_errors_are_422(client: httpx.AsyncClient, body: Any) -> None:
    r = await post(client, body)
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "validation-error"


async def test_invalid_json_is_422(client: httpx.AsyncClient) -> None:
    r = await client.post(
        URL, content=b"[{", headers={**INGEST, "Content-Type": "application/json"}
    )
    assert (r.status_code, r.json()["code"]) == (422, "validation-error")


async def test_key_matrix(client: httpx.AsyncClient) -> None:
    routes = [
        ("post", URL, INGEST, {"json": [event(1)]}),
        ("get", "/api/v1/operator/rejects", OPERATOR, {}),
        ("get", "/api/v1/operator/ingestion/health", OPERATOR, {}),
    ]
    keys = {"ingest": INGEST, "admin": ADMIN, "operator": OPERATOR}
    for method, url, allowed, kwargs in routes:
        assert (await client.request(method, url, **kwargs)).status_code == 401
        for headers in keys.values():
            r = await client.request(method, url, headers=headers, **kwargs)
            if headers is allowed:
                assert r.status_code == (202 if method == "post" else 200)
            else:
                assert (r.status_code, r.json()["code"]) == (401, "unauthorized")


async def test_busy_when_the_semaphore_is_exhausted(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    app.state.ingest_semaphore = asyncio.Semaphore(0)
    r = await post(client, [event(1)])
    assert (r.status_code, r.json()["code"], r.headers["retry-after"]) == (429, "ingest-busy", "1")
    assert (await lot(client))["version"] == 0


async def test_busy_when_a_lot_lock_times_out(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LockNotAvailable(Exception):
        sqlstate = "55P03"

    async def boom(*_: Any) -> None:
        raise DBAPIError("SELECT ...", {}, LockNotAvailable())

    monkeypatch.setattr(IngestionService, "ingest", boom)
    r = await post(client, [event(1)])
    assert (r.status_code, r.json()["code"], r.headers["retry-after"]) == (429, "ingest-busy", "1")


async def test_database_down_is_503(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_: Any) -> None:
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError())

    monkeypatch.setattr(IngestionService, "ingest", boom)
    r = await post(client, [event(1)])
    assert (r.status_code, r.json()["code"], r.headers["retry-after"]) == (
        503,
        "database-unavailable",
        "5",
    )


async def test_other_database_errors_stay_500(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_: Any) -> None:
        raise DBAPIError("SELECT 1", {}, ValueError("syntax"))

    monkeypatch.setattr(IngestionService, "ingest", boom)
    assert (await post(client, [event(1)])).status_code == 500


async def test_draining_is_503(app: FastAPI, client: httpx.AsyncClient) -> None:
    app.state.draining = True
    r = await post(client, [event(1)])
    assert (r.status_code, r.json()["code"], r.headers["retry-after"]) == (
        503,
        "service-draining",
        "5",
    )


async def test_out_of_order_arrival_across_batches(client: httpx.AsyncClient) -> None:
    entry, exit_ = event(1, ago_min=30), event(2, "exit", ago_min=10)
    await post(client, [exit_])  # exit_only: no occupancy effect
    assert (await lot(client))["occupied"] == 0
    await post(client, [entry])  # the entry arrives late: exit_only becomes closed
    assert (await lot(client))["occupied"] == 0
    # e1, x1, e2 in a different arrival order still leaves the second entry open.
    e2 = event(3, student=REF_B, ago_min=5)
    x1 = event(4, "exit", student=REF_B, ago_min=15)
    e1 = event(5, student=REF_B, ago_min=25)
    await post(client, [e2])
    await post(client, [x1])
    assert (await lot(client))["occupied"] == 1
    await post(client, [e1])
    assert (await lot(client))["occupied"] == 1


async def test_gate_freshness_never_moves_backwards(client: httpx.AsyncClient) -> None:
    await post(client, [event(1, ago_min=5)])
    await post(client, [event(2, student=REF_B, ago_min=20)])
    health = (await client.get("/api/v1/operator/ingestion/health", headers=OPERATOR)).json()
    gates = {g["gate_id"]: g for g in health["gates"]}
    assert gates["principal-entrada"]["last_event_at"].startswith("2026-10-28T13:55:00")
    assert gates["principal-entrada"]["last_received_at"].startswith("2026-10-28T14:00:00")
    assert gates["anexo-saida"]["last_event_at"] is None
    assert list(gates) == sorted(gates)
    assert (await lot(client))["last_event_at"].startswith("2026-10-28T13:55:00")


async def test_oversized_payload_is_capped(client: httpx.AsyncClient) -> None:
    bad = event(1, student=REF_A) | {"junk": "x" * 6000}
    await post(client, [bad])
    page = (await client.get("/api/v1/operator/rejects", headers=OPERATOR)).json()
    payload = page["items"][0]["payload"]
    assert payload["truncated"] is True
    assert len(payload["preview"]) <= 1000


async def test_rejects_pagination_and_filters(client: httpx.AsyncClient) -> None:
    for n in range(5):
        await post(
            client, [event(10 + n, student="stu_ffffffffffff"), event(20 + n, gate_id="ghost-gate")]
        )
    url = "/api/v1/operator/rejects"
    first = (await client.get(url, params={"limit": 4}, headers=OPERATOR)).json()
    assert len(first["items"]) == 4
    assert first["next_cursor"]
    seen = [i["reject_id"] for i in first["items"]]
    assert seen == sorted(seen, reverse=True)
    rest = (
        await client.get(
            url, params={"limit": 50, "cursor": first["next_cursor"]}, headers=OPERATOR
        )
    ).json()
    assert rest["next_cursor"] is None
    ids = seen + [i["reject_id"] for i in rest["items"]]
    assert len(ids) == len(set(ids)) == 10
    item = first["items"][0]
    assert item["reason"] in {"unknown_student", "unknown_gate"}
    assert item["service_date"] == "2026-10-28"
    only = (await client.get(url, params={"reason": "unknown_gate"}, headers=OPERATOR)).json()
    assert {i["reason"] for i in only["items"]} == {"unknown_gate"}
    assert len(only["items"]) == 5
    later = (
        await client.get(url, params={"since": iso(NOW.replace(hour=15))}, headers=OPERATOR)
    ).json()
    assert later["items"] == []


@pytest.mark.parametrize(
    "params",
    [
        {"cursor": "###"},
        {"cursor": "bm90LWEtY3Vyc29y"},
        {"limit": 0},
        {"limit": 201},
        {"reason": "x"},
    ],
)
async def test_rejects_bad_query_is_422(client: httpx.AsyncClient, params: dict[str, Any]) -> None:
    r = await client.get("/api/v1/operator/rejects", params=params, headers=OPERATOR)
    assert (r.status_code, r.json()["code"]) == (422, "validation-error")


async def test_ingestion_health_numbers(client: httpx.AsyncClient) -> None:
    empty = (await client.get("/api/v1/operator/ingestion/health", headers=OPERATOR)).json()
    assert empty["per_minute"] == []
    assert empty["lag"] == {"avg_s": None, "max_s": None}
    assert empty["last_batch_at"] is None
    assert len(empty["gates"]) == 4

    await post(client, [event(1, ago_min=10), event(2, student=REF_B, ago_min=20)])
    await post(client, [event(1, ago_min=10), event(3, student="stu_ffffffffffff")])
    health = (await client.get("/api/v1/operator/ingestion/health", headers=OPERATOR)).json()
    assert len(health["per_minute"]) == 1
    minute = health["per_minute"][0]
    assert minute["minute"].startswith("2026-10-28T14:00:00")
    assert (minute["accepted"], minute["duplicates"], minute["rejected"]) == (2, 1, 1)
    assert health["rejects_by_reason"] == {"unknown_student": 1}
    assert health["lag"] == {"avg_s": 900.0, "max_s": 1200.0}
    assert health["last_batch_at"].startswith("2026-10-28T14:00:00")
    # A one-minute window ending now still includes batches received at exactly now.
    narrow = (
        await client.get(
            "/api/v1/operator/ingestion/health", params={"window_min": 1}, headers=OPERATOR
        )
    ).json()
    assert len(narrow["per_minute"]) == 1
    for bad in (0, 241):
        r = await client.get(
            "/api/v1/operator/ingestion/health", params={"window_min": bad}, headers=OPERATOR
        )
        assert r.status_code == 422


async def test_availability_unknown_lot_is_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/lots/ghost/availability")
    assert (r.status_code, r.json()["code"]) == (404, "lot-not-found")


async def test_lot_without_a_started_capacity_has_no_free_figure(
    client: httpx.AsyncClient,
) -> None:
    body = {"name": "Future", "capacity_history": [{"valid_from": "2027-01-01", "capacity": 10}]}
    await client.put("/api/v1/admin/lots/future", json=body, headers=ADMIN)
    out = await lot(client, "future")
    assert (out["capacity"], out["free"], out["occupancy_pct"], out["over_capacity"]) == (
        None,
        None,
        None,
        False,
    )


async def test_metrics_and_log_line(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="campus_parking.ingestion.service")
    await post(client, [event(1), event(2, student="stu_ffffffffffff")])
    record = next(r for r in caplog.records if r.message == "ingest batch")
    assert (record.accepted, record.rejected, record.items) == (1, 1, 2)  # type: ignore[attr-defined]
    text = (await client.get("/metrics")).text
    for name in (
        'parking_events_ingested_total{lot="principal"}',
        'parking_events_rejected_total{reason="unknown_student"}',
        "parking_event_lag_seconds_bucket",
    ):
        assert name in text
    assert "stu_" not in "".join(r.getMessage() + str(r.__dict__) for r in caplog.records)


async def test_openapi_documents_the_contract(app: FastAPI) -> None:
    doc = app.openapi()
    component = doc["components"]["schemas"]["GateEvent"]
    assert component["additionalProperties"] is False
    assert set(component["required"]) == {
        "schema_version",
        "event_id",
        "gate_id",
        "lot_id",
        "direction",
        "student_ref",
        "occurred_at",
    }
    body = doc["paths"]["/api/v1/events"]["post"]["requestBody"]
    assert body["required"] is True
    schema = body["content"]["application/json"]["schema"]
    assert schema == {
        "type": "array",
        "minItems": 1,
        "maxItems": 1000,
        "items": {"$ref": "#/components/schemas/GateEvent"},
    }
    responses = doc["paths"]["/api/v1/events"]["post"]["responses"]
    assert set(responses) == {"202", "401", "422", "429", "503"}
    assert "application/problem+json" in responses["429"]["content"]
    assert set(doc["paths"]) >= {
        "/api/v1/availability",
        "/api/v1/lots/{lot_id}/availability",
        "/api/v1/operator/rejects",
        "/api/v1/operator/ingestion/health",
    }
