from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from campus_parking.main import create_app
from tests.conftest import ORIGIN, make_settings


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz_ok(client: httpx.AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


async def test_readyz_503_while_draining(app: FastAPI, client: httpx.AsyncClient) -> None:
    app.state.draining = True
    r = await client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["code"] == "service-draining"
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_readyz_503_when_database_unreachable(client_for: Callable[[FastAPI], Any]) -> None:
    app = create_app(make_settings(database_url="sqlite+aiosqlite:////nonexistent-dir/x/y.db"))
    async with client_for(app) as c:
        r = await c.get("/readyz")
    assert r.status_code == 503
    assert r.json()["code"] == "database-unavailable"


async def test_draining_flag_set_after_shutdown(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        assert app.state.draining is False
    assert app.state.draining is True


async def test_metrics_use_route_templates(client: httpx.AsyncClient) -> None:
    await client.get("/healthz")
    await client.get("/some/unknown/path/12345")
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "# TYPE http_request_duration_seconds histogram" in r.text
    assert 'route="/healthz"' in r.text
    assert 'route="unmatched"' in r.text
    assert "12345" not in r.text


async def test_metrics_not_in_openapi(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert "/metrics" not in spec["paths"]
    assert spec["info"]["title"] == "UNDB Campus Parking API"
    assert "/healthz" in spec["paths"]


async def test_cors_preflight_allowed_origin(client: httpx.AsyncClient) -> None:
    r = await client.options(
        "/healthz",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-API-Key, Content-Type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in r.headers


async def test_cors_preflight_refused_origin(client: httpx.AsyncClient) -> None:
    r = await client.options(
        "/healthz",
        headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


async def test_cors_exposes_request_id(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"Origin": ORIGIN})
    assert "X-Request-ID" in r.headers["access-control-expose-headers"]


@pytest.mark.parametrize("method", ["DELETE", "PATCH"])
async def test_cors_refuses_other_methods(client: httpx.AsyncClient, method: str) -> None:
    r = await client.options(
        "/healthz", headers={"Origin": ORIGIN, "Access-Control-Request-Method": method}
    )
    assert r.status_code == 400
