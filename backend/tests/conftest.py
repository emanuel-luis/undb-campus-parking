import os
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from campus_parking.core.settings import Settings, get_settings
from campus_parking.main import create_app

KEY = "k" * 32
ORIGIN = "http://app.example.test"


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "cors_origins": [ORIGIN],
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No test reads the real PARKING_* environment or a stray .env."""
    for name in [n for n in os.environ if n.startswith("PARKING_")]:
        monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client_for() -> Callable[[FastAPI], Any]:
    """Return an async context manager that runs the app lifespan and yields a client."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                yield c

    return _client


@pytest.fixture
async def client(
    app: FastAPI, client_for: Callable[[FastAPI], Any]
) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(app) as c:
        yield c
