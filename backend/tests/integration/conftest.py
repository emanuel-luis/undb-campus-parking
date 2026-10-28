"""Fixtures for tests that need a real Postgres.

Set TEST_DATABASE_URL (a `postgresql+asyncpg://` URL; `make test-integration` defaults it to the
compose Postgres). The database it names is created when missing and its `public` schema is
dropped and recreated before every test, so it must be a throwaway database, never the
application one. Without the variable every test here is skipped.
"""

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

HERE = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.is_relative_to(HERE):
            item.add_marker(pytest.mark.integration)


async def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await admin.dispose()


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def fetch_column(url: str, sql: str) -> list[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return [str(v) for v in (await conn.execute(text(sql))).scalars()]
    finally:
        await engine.dispose()


def check_throwaway_database(url: str, app_url: str | None) -> None:
    """Refuse any database the suite could destroy by mistake: it must be named `*_test` and
    must not be the application database. Exits the run (a failure, not a skip)."""
    target = make_url(url)
    name = target.database or ""
    if not name.endswith("_test"):
        pytest.exit(
            f"TEST_DATABASE_URL names database {name!r}; the integration suite drops its public "
            "schema, so the name must end with '_test'.",
            returncode=2,
        )
    if app_url:
        app = make_url(app_url)
        if (app.host, app.port, app.database) == (target.host, target.port, target.database):
            pytest.exit(
                "TEST_DATABASE_URL points at the application database (PARKING_DATABASE_URL).",
                returncode=2,
            )


@pytest.fixture(scope="session")
def pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    check_throwaway_database(url, os.environ.get("PARKING_DATABASE_URL"))
    asyncio.run(_ensure_database(url))
    return url


@pytest.fixture
def pg_database(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """An empty schema, and PARKING_DATABASE_URL pointing at it (for alembic's env.py)."""
    asyncio.run(_reset_schema(pg_url))
    monkeypatch.setenv("PARKING_DATABASE_URL", pg_url)
    yield pg_url
