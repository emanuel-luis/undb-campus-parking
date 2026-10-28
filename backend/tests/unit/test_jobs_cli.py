"""close-day, export and worker CLI commands against a SQLite file."""

import asyncio
import signal
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from campus_parking import cli
from campus_parking.core.db import Base
from campus_parking.jobs import worker as worker_module
from campus_parking.jobs.models import PipelineRun
from tests.conftest import make_settings

PAST = date(2020, 1, 1)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "parking.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    monkeypatch.setenv("PARKING_DATABASE_URL", f"sqlite+aiosqlite:///{path.as_posix()}")
    monkeypatch.setenv("PARKING_LAKE_DIR", str(tmp_path / "lake"))
    monkeypatch.setenv("PARKING_LOG_FORMAT", "text")
    return path


def runs(db: Path) -> list[PipelineRun]:
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        out = list(s.scalars(select(PipelineRun).order_by(PipelineRun.requested_at)))
    engine.dispose()
    return out


def test_close_day_runs_and_records_a_cli_run(db: Path, tmp_path: Path) -> None:
    assert cli.main(["close-day", "--date", PAST.isoformat()]) == 0
    (run,) = runs(db)
    assert (run.job, run.trigger, run.status, run.service_date) == (
        "close_day",
        "cli",
        "succeeded",
        PAST,
    )
    assert run.metrics["export_status"] == "written"
    assert (tmp_path / "lake" / "raw" / "gate_events" / "service_date=2020-01-01").is_dir()


def test_warehouse_command_and_worker_once_warehouse(db: Path, fake_dbt: list[str]) -> None:
    assert cli.main(["warehouse", "--full", "--date", PAST.isoformat()]) == 0
    assert cli.main(["worker", "--once", "warehouse"]) == 0
    assert fake_dbt == ["full", "incremental"]
    assert [(r.job, r.mode, r.status) for r in runs(db)] == [
        ("warehouse", "full", "succeeded"),
        ("warehouse", "incremental", "succeeded"),
    ]
    assert runs(db)[1].service_date in {
        datetime.now(UTC).date(),
        datetime.now(UTC).date() - timedelta(days=1),
    }  # today in the service time zone


def test_warehouse_refuses_a_future_date(
    db: Path, fake_dbt: list[str], capfd: pytest.CaptureFixture[str]
) -> None:
    future = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    assert cli.main(["warehouse", "--date", future]) == 2
    assert cli.main(["worker", "--once", "warehouse", "--date", future]) == 2
    out = capfd.readouterr()
    assert "in the future" in out.out + out.err
    assert fake_dbt == []
    assert runs(db) == []


def test_export_is_a_close_day_run_in_export_mode(db: Path) -> None:
    assert cli.main(["export", "--date", PAST.isoformat()]) == 0
    (run,) = runs(db)
    assert (run.job, run.mode, run.status) == ("close_day", "export", "succeeded")


def test_the_default_date_is_yesterday_and_worker_once_runs_daily(db: Path) -> None:
    assert cli.main(["worker", "--once", "daily"]) == 0
    jobs = {r.job: r for r in runs(db)}
    assert jobs["daily"].status == "succeeded"
    assert jobs["close_day"].parent_run_id == jobs["daily"].run_id
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    assert jobs["daily"].service_date in {yesterday, yesterday - timedelta(days=1)}


def test_a_day_that_is_not_over_exits_2(db: Path) -> None:
    future = (datetime.now(UTC) + timedelta(days=3)).date()
    assert cli.main(["close-day", "--date", future.isoformat()]) == 2
    (run,) = runs(db)
    assert run.status == "failed"


def test_an_active_run_exits_1(db: Path) -> None:
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        s.add(
            PipelineRun(
                run_id=uuid4(),
                job="close_day",
                trigger="operator",
                service_date=PAST,
                status="queued",
                requested_at=datetime.now(UTC),
            )
        )
        s.commit()
    engine.dispose()
    assert cli.main(["close-day", "--date", PAST.isoformat()]) == 1
    assert [r.status for r in runs(db)] == ["queued"]


def test_a_failing_job_exits_1(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_module, "close_day", boom)
    assert cli.main(["close-day", "--date", PAST.isoformat()]) == 1
    (run,) = runs(db)
    assert (run.status, run.error) == ("failed", "RuntimeError: boom")


def test_worker_without_once_runs_the_serve_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[object] = []

    async def fake(settings: object) -> int:
        called.append(settings)
        return 0

    monkeypatch.setattr(cli, "run_worker", fake)
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)
    assert cli.main(["worker"]) == 0
    assert len(called) == 1


async def test_run_worker_serves_until_stopped_and_disposes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started: list[int] = []
    monkeypatch.setattr(
        worker_module, "start_http_server", lambda port, registry: started.append(port)
    )
    monkeypatch.setattr(worker_module, "_install_stop", lambda stop: stop.set())
    served: list[bool] = []

    async def serve(self: worker_module.Worker, stop: asyncio.Event) -> None:
        served.append(stop.is_set())

    monkeypatch.setattr(worker_module.Worker, "serve", serve)
    settings = make_settings(worker_metrics_port=9123, lake_dir=tmp_path)
    assert await worker_module.run_worker(settings) == 0
    assert (started, served) == ([9123], [True])
    started.clear()
    assert await worker_module.run_worker(make_settings(worker_metrics_port=0)) == 0
    assert started == []  # port 0 turns the exporter off


async def test_stop_signals_fall_back_to_signal_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()

    def unsupported(*_: Any) -> None:
        raise NotImplementedError

    handlers: dict[int, Callable[..., None]] = {}
    monkeypatch.setattr(loop, "add_signal_handler", unsupported)
    monkeypatch.setattr(signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    stop = asyncio.Event()
    worker_module._install_stop(stop)
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM}
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    await asyncio.sleep(0.01)
    assert stop.is_set()
