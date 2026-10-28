import httpx
import pytest
from fastapi import Depends, FastAPI

from campus_parking.core.security import require_admin_key, require_ingest_key, require_operator_key
from campus_parking.main import create_app
from tests.conftest import make_settings

KEYS = {"ingest": "i" * 32, "admin": "a" * 32, "operator": "o" * 32}


@pytest.fixture
def app() -> FastAPI:
    app = create_app(
        make_settings(
            ingest_api_key=KEYS["ingest"],
            admin_api_key=KEYS["admin"],
            operator_api_key=KEYS["operator"],
        )
    )
    for name, dep in (
        ("ingest", require_ingest_key),
        ("admin", require_admin_key),
        ("operator", require_operator_key),
    ):
        app.add_api_route(f"/t/{name}", _ok, dependencies=[Depends(dep)])
    return app


async def _ok() -> dict[str, bool]:
    return {"ok": True}


@pytest.mark.parametrize("role", KEYS)
async def test_key_matrix(client: httpx.AsyncClient, role: str) -> None:
    url = f"/t/{role}"
    missing = await client.get(url)
    assert missing.status_code == 401
    assert missing.json()["code"] == "unauthorized"
    assert missing.headers["www-authenticate"] == "ApiKey"

    wrong = await client.get(url, headers={"X-API-Key": "x" * 32})
    assert wrong.status_code == 401

    for other, key in KEYS.items():
        if other != role:
            assert (await client.get(url, headers={"X-API-Key": key})).status_code == 401

    right = await client.get(url, headers={"X-API-Key": KEYS[role]})
    assert right.status_code == 200


async def test_key_not_configured_is_401(client_for) -> None:  # type: ignore[no-untyped-def]
    app = create_app(make_settings())
    app.add_api_route("/t/admin", _ok, dependencies=[Depends(require_admin_key)])
    async with client_for(app) as c:
        r = await c.get("/t/admin", headers={"X-API-Key": "a" * 32})
    assert r.status_code == 401
