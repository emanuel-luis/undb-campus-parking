"""Live availability on Postgres: writes made by another connection reach the socket."""

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.testclient import TestClient, WebSocketTestSession

from alembic import command
from campus_parking.cli import alembic_config
from campus_parking.main import create_app
from tests.conftest import make_settings
from tests.helpers import ADMIN

URL = "/api/v1/live/availability"
BUMP = "UPDATE lot_occupancy SET occupied = occupied + 1, version = version + 1, updated_at = now()"


@pytest.fixture
def live(pg_database: str) -> Iterator[tuple[TestClient, str]]:
    command.upgrade(alembic_config(), "head")
    app: FastAPI = create_app(
        make_settings(
            database_url=pg_database,
            admin_api_key=ADMIN["X-API-Key"],
            live_poll_interval_s=0.2,
            live_heartbeat_s=0.5,
        )
    )
    with TestClient(app) as client:
        body = {"name": "Main", "capacity_history": [{"valid_from": "2026-01-01", "capacity": 10}]}
        assert client.put("/api/v1/admin/lots/main", json=body, headers=ADMIN).status_code == 201
        yield client, pg_database


def leave(ws: WebSocketTestSession) -> None:
    """Let the handler finish before TestClient cancels it (see tests/api/test_live.py)."""
    ws.close()
    time.sleep(0.2)


def versions(message: dict[str, Any]) -> list[int]:
    return [lot["version"] for lot in message["lots"]]


async def write_from_another_connection(url: str, commit: bool) -> None:
    """What the worker or another replica does: a separate engine, a separate transaction."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text(BUMP))
            if commit:
                await conn.commit()
            else:
                await conn.rollback()
    finally:
        await engine.dispose()


def test_committed_write_from_another_connection_reaches_the_client(
    live: tuple[TestClient, str],
) -> None:
    client, url = live
    with client.websocket_connect(URL) as ws:
        assert versions(ws.receive_json()) == [0]
        asyncio.run(write_from_another_connection(url, commit=True))
        deadline = time.monotonic() + 2
        message = ws.receive_json()
        while versions(message) != [1] and time.monotonic() < deadline:
            message = ws.receive_json()
        assert versions(message) == [1]
        assert message["lots"][0]["occupied"] == 1
        assert time.monotonic() < deadline
        leave(ws)


def test_rolled_back_write_sends_nothing_new(live: tuple[TestClient, str]) -> None:
    client, url = live
    with client.websocket_connect(URL) as ws:
        ws.receive_json()
        asyncio.run(write_from_another_connection(url, commit=False))
        seen = [versions(ws.receive_json()) for _ in range(4)]  # heartbeats only
        leave(ws)
    assert seen == [[0]] * 4
