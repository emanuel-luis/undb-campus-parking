import json
import logging
import uuid

import httpx
import pytest
from fastapi import FastAPI

from campus_parking.core.problems import DomainError, Unauthorized
from tests.conftest import ORIGIN


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    @app.get("/t/domain")
    async def domain() -> None:
        raise Unauthorized("nope")

    @app.get("/t/custom")
    async def custom() -> None:
        raise DomainError()

    @app.get("/t/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/t/int/{n}")
    async def int_route(n: int) -> dict[str, int]:
        return {"n": n}

    return app


def assert_problem(r: httpx.Response, status: int, code: str) -> dict[str, object]:
    assert r.status_code == status
    assert r.headers["content-type"].startswith("application/problem+json")
    body: dict[str, object] = r.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["type"] == f"/problems/{code}"
    assert body["request_id"] == r.headers["x-request-id"]
    assert {"title", "detail", "instance"} <= body.keys()
    return body


async def test_404(client: httpx.AsyncClient) -> None:
    body = assert_problem(await client.get("/nope"), 404, "not-found")
    assert body["instance"] == "/nope"


async def test_405(client: httpx.AsyncClient) -> None:
    r = await client.post("/healthz")
    assert_problem(r, 405, "method-not-allowed")
    assert "GET" in r.headers["allow"]


async def test_422_has_errors(client: httpx.AsyncClient) -> None:
    body = assert_problem(await client.get("/t/int/abc"), 422, "validation-error")
    errors = body["errors"]
    assert isinstance(errors, list) and errors[0]["loc"] == ["path", "n"]


async def test_domain_error(client: httpx.AsyncClient) -> None:
    r = await client.get("/t/domain")
    body = assert_problem(r, 401, "unauthorized")
    assert body["detail"] == "nope"
    assert r.headers["www-authenticate"] == "ApiKey"


async def test_domain_error_defaults(client: httpx.AsyncClient) -> None:
    assert_problem(await client.get("/t/custom"), 500, "internal-error")


async def test_unhandled_error_hides_internals(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        r = await client.get("/t/boom")
    body = assert_problem(r, 500, "internal-error")
    assert "secret internal detail" not in json.dumps(body)
    assert "Traceback" not in r.text
    record = next(rec for rec in caplog.records if rec.getMessage() == "unhandled exception")
    assert record.exc_info is not None


async def test_request_id_generated_and_echoed(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert str(uuid.UUID(r.headers["x-request-id"]))
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123.ok"})
    assert r.headers["x-request-id"] == "abc-123.ok"


@pytest.mark.parametrize("bad", ["has space", "x" * 129, "semi;colon", "new\tline"])
async def test_invalid_request_id_replaced(client: httpx.AsyncClient, bad: str) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": bad})
    assert r.headers["x-request-id"] != bad
    assert str(uuid.UUID(r.headers["x-request-id"]))


async def test_access_log_line(client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="campus_parking.access"):
        await client.get("/healthz", headers={"X-Request-ID": "log-1"})
    rec = next(r for r in caplog.records if r.name == "campus_parking.access")
    assert (rec.method, rec.route, rec.status) == ("GET", "/healthz", 200)  # type: ignore[attr-defined]
    assert rec.duration_ms >= 0  # type: ignore[attr-defined]


async def test_500_carries_cors_and_request_id(client: httpx.AsyncClient) -> None:
    r = await client.get("/t/boom", headers={"Origin": ORIGIN})
    assert_problem(r, 500, "internal-error")
    assert r.headers["access-control-allow-origin"] == ORIGIN
