from datetime import date

import pytest
from fake_api import ADMIN, INGEST, FakeApi
from pydantic import SecretStr

from turnstile_generator.client import ParkingClient
from turnstile_generator.settings import Settings

TODAY = date(2026, 10, 28)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Settings:
    for name in list(__import__("os").environ):
        if name.startswith("GENERATOR_"):
            monkeypatch.delenv(name)
    return Settings(_env_file=None, students=300, heartbeat_file=tmp_path / "hb")


@pytest.fixture
def api() -> FakeApi:
    return FakeApi(TODAY)


@pytest.fixture
def client(api: FakeApi) -> ParkingClient:
    return ParkingClient(
        "http://test",
        ingest_key=SecretStr(INGEST),
        admin_key=SecretStr(ADMIN),
        transport=api.transport(),
    )
