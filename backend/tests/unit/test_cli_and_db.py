import json
import signal
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import create_async_engine

from campus_parking import cli
from campus_parking.core.db import Base, UTCDateTime, get_session


def _sorted_keys(obj: object) -> bool:
    if isinstance(obj, dict):
        keys = list(obj)
        return keys == sorted(keys) and all(_sorted_keys(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_sorted_keys(v) for v in obj)
    return True


def test_openapi_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["openapi"]) == 0
    out = capsys.readouterr().out
    assert out.endswith("}\n")
    doc = json.loads(out)
    assert doc["info"]["title"] == "UNDB Campus Parking API"
    assert _sorted_keys(doc)


def test_openapi_file(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    assert cli.main(["openapi", "--output", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["openapi"].startswith("3.")


def test_serve_builds_config_and_runs_draining_server(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[cli.DrainingServer] = []
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)
    monkeypatch.setattr(cli.DrainingServer, "run", lambda self: ran.append(self))
    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "9000"]) == 0
    (server,) = ran
    assert server.config.port == 9000
    assert server.config.timeout_graceful_shutdown == 15
    assert server.config.access_log is False
    assert server.config.app is server.parking_app


async def test_handle_exit_flips_draining_and_readyz_503(client_for) -> None:  # type: ignore[no-untyped-def]
    from campus_parking.main import create_app
    from tests.conftest import make_settings

    app = create_app(make_settings())
    server = cli.DrainingServer(uvicorn.Config(app), app)
    async with client_for(app) as c:
        assert (await c.get("/readyz")).status_code == 200
        server.handle_exit(signal.SIGTERM, None)
        assert app.state.draining is True
        assert server.should_exit is True
        r = await c.get("/readyz")
    assert r.status_code == 503
    assert r.json()["code"] == "service-draining"


def test_naming_convention_applies() -> None:
    t = Table("t", Base.metadata, Column("id", Integer, primary_key=True))
    try:
        assert t.primary_key.name == "pk_t"
    finally:
        Base.metadata.remove(t)


async def test_utc_datetime_roundtrip() -> None:
    md = MetaData()
    t = Table("ev", md, Column("id", Integer, primary_key=True), Column("at", UTCDateTime))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    local = datetime(2026, 10, 28, 8, 0, tzinfo=timezone(timedelta(hours=-3)))
    async with engine.begin() as conn:
        await conn.run_sync(md.create_all)
        await conn.execute(insert(t), [{"id": 1, "at": local}, {"id": 2, "at": None}])
        rows = (await conn.execute(select(t.c.at).order_by(t.c.id))).scalars().all()
        raw = (await conn.execute(text("SELECT at FROM ev WHERE id = 1"))).scalar_one()
        with pytest.raises(StatementError, match="naive"):
            await conn.execute(insert(t), {"id": 3, "at": datetime(2026, 1, 1)})
    await engine.dispose()
    assert rows[0] == local and rows[0].tzinfo is UTC  # type: ignore[union-attr]
    assert rows[1] is None
    assert str(raw).startswith("2026-10-28 11:00:00")


async def test_get_session_yields_session(app) -> None:  # type: ignore[no-untyped-def]
    class Req:
        pass

    req = Req()
    req.app = app  # type: ignore[attr-defined]
    async with app.router.lifespan_context(app):
        gen = get_session(req)  # type: ignore[arg-type]
        session = await anext(gen)
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        await gen.aclose()
