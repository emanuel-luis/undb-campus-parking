"""The live availability WebSocket, through Starlette's TestClient (SQLite file database)."""

import contextlib
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from campus_parking.core.db import Base
from campus_parking.main import create_app
from tests.conftest import make_settings
from tests.helpers import ADMIN, GATES, INGEST, REF_A, event

URL = "/api/v1/live/availability"
EVENTS = "/api/v1/events"


def build(tmp_path: Path, **overrides: Any) -> FastAPI:
    settings: dict[str, Any] = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'live.db'}",
        "ingest_api_key": INGEST["X-API-Key"],
        "admin_api_key": ADMIN["X-API-Key"],
        "live_poll_interval_s": 0.05,
        "live_heartbeat_s": 30,
    }
    return create_app(make_settings(**{**settings, **overrides}))


def started(app: FastAPI) -> TestClient:
    """A TestClient with the lifespan running, the schema created and a small registry."""
    client = TestClient(app)
    client.__enter__()

    async def create() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    assert client.portal is not None
    client.portal.call(create)
    for lot in ("principal", "anexo"):
        body = {"name": lot, "capacity_history": [{"valid_from": "2026-01-01", "capacity": 3}]}
        assert client.put(f"/api/v1/admin/lots/{lot}", json=body, headers=ADMIN).status_code == 201
    for gate_id, (lot, direction) in GATES.items():
        body = {"lot_id": lot, "direction": direction, "name": gate_id}
        r = client.put(f"/api/v1/admin/gates/{gate_id}", json=body, headers=ADMIN)
        assert r.status_code == 201
    refs = {"student_refs": [REF_A]}
    assert client.post("/api/v1/admin/students/bulk", json=refs, headers=ADMIN).status_code == 200
    return client


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    c = started(build(tmp_path))
    yield c
    c.__exit__(None, None, None)


@contextlib.contextmanager
def connect(client: TestClient) -> Iterator[WebSocketTestSession]:
    """Open the socket and, on the way out, let the handler finish before TestClient cancels
    its task (it cancels right after sending the disconnect, which would race the cleanup)."""
    with client.websocket_connect(URL) as ws:
        try:
            yield ws
        finally:
            ws.close()
            time.sleep(0.2)


def versions(message: dict[str, Any]) -> dict[str, int]:
    return {lot["lot_id"]: lot["version"] for lot in message["lots"]}


def metrics_text(client: TestClient) -> str:
    return client.get("/metrics").text


def test_snapshot_on_connect_matches_rest(client: TestClient) -> None:
    with connect(client) as ws:
        first = ws.receive_json()
    assert [lot["lot_id"] for lot in first["lots"]] == ["anexo", "principal"]
    assert first["lots"] == client.get("/api/v1/availability").json()["lots"]


def test_ingest_reaches_the_client(client: TestClient) -> None:
    with connect(client) as ws:
        assert versions(ws.receive_json()) == {"anexo": 0, "principal": 0}
        ws.send_text("ignored")  # client frames are read and discarded
        assert "parking_live_connections 1.0" in metrics_text(client)
        r = client.post(EVENTS, json=[event(2, ago_min=0.1)], headers=INGEST)
        assert r.json()["accepted"] == 1
        for _ in range(100):
            message = ws.receive_json()
            if versions(message)["principal"] == 1:
                break
        else:
            pytest.fail("no snapshot with the new version")
        assert message["lots"][1]["occupied"] == 1
    assert 'parking_lot_occupied{lot="principal"} 1.0' in metrics_text(client)


def test_heartbeat_without_changes(tmp_path: Path) -> None:
    c = started(build(tmp_path, live_heartbeat_s=0.2))
    try:
        with connect(c) as ws:
            first = ws.receive_json()
            second = ws.receive_json()  # nothing changed: this one is the heartbeat
        assert versions(first) == versions(second)
        assert 'parking_lot_capacity{lot="principal"} 3.0' in metrics_text(c)
    finally:
        c.__exit__(None, None, None)


def test_over_the_cap_closes_with_1013(tmp_path: Path) -> None:
    c = started(build(tmp_path, live_max_connections=1))
    try:
        with connect(c) as ws:
            ws.receive_json()
            with connect(c) as extra, pytest.raises(WebSocketDisconnect) as err:
                extra.receive_json()
            assert err.value.code == 1013
    finally:
        c.__exit__(None, None, None)


def test_shutdown_closes_with_1001(client: TestClient) -> None:
    app: FastAPI = client.app  # type: ignore[assignment]
    assert client.portal is not None
    with connect(client) as ws:
        ws.receive_json()
        client.portal.call(app.state.hub.close)
        with pytest.raises(WebSocketDisconnect) as err:
            ws.receive_json()
        assert err.value.code == 1001
    with connect(client) as late, pytest.raises(WebSocketDisconnect) as err:
        late.receive_json()
    assert err.value.code == 1001


def test_openapi_documents_the_socket(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    (socket,) = schema["x-websockets"]
    assert socket["path"] == URL
    assert socket["message"] == {"$ref": "#/components/schemas/AvailabilitySnapshot"}
    assert set(socket["close_codes"]) == {"1001", "1012", "1013"}
    assert socket["heartbeat_s"] == 30
    assert "AvailabilitySnapshot" in schema["components"]["schemas"]
