import os
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import TODAY
from fake_api import ADMIN, INGEST, FakeApi

from turnstile_generator import cli, heartbeat, registry
from turnstile_generator.client import ParkingClient
from turnstile_generator.live import run_live
from turnstile_generator.logging import JsonFormatter
from turnstile_generator.sender import Sender
from turnstile_generator.settings import Settings

START = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)  # 07:00 in Sao Luis, the morning peak


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _live(client, api, settings, minutes=3, stop=None, **kw):
    clock = Clock()
    settings = settings.model_copy(update={"fault_out_of_order_rate": 0.0, **kw})
    specs = registry.specs_from_api(client.get_lots())
    sender = Sender(client, sleeper=clock.sleep)
    totals = run_live(
        sender,
        settings,
        specs,
        scenario="normal",
        rate_multiplier=1.0,
        duration_s=minutes * 60,
        clock=clock,
        sleeper=clock.sleep,
        stop=stop,
    )
    return totals, clock


def _bootstrapped(client, api, settings):
    from test_bootstrap_backfill import _bootstrap

    _bootstrap(client, settings)


def test_live_sends_events_in_real_time_batches(client: ParkingClient, api: FakeApi, settings):
    _bootstrapped(client, api, settings)
    totals, clock = _live(client, api, settings)
    assert totals.received > 3  # morning peak, three minutes, both lots
    assert totals.accepted == len(api.events)
    assert max(api.batch_sizes) <= settings.batch_size
    assert all(
        START - timedelta(minutes=1) <= datetime.fromisoformat(e["occurred_at"]) <= clock.now
        for e in api.events.values()
    )


def test_live_sends_at_least_the_minimum_rate_at_night(client, api, settings):
    _bootstrapped(client, api, settings)
    clock = Clock()
    clock.now = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)  # 03:00 local
    specs = registry.specs_from_api(client.get_lots())
    totals = run_live(
        Sender(client, sleeper=clock.sleep),
        settings.model_copy(update={"min_rate_per_min": 6.0}),
        specs,
        scenario="quiet",
        rate_multiplier=1.0,
        duration_s=300,
        clock=clock,
        sleeper=clock.sleep,
    )
    assert totals.received >= 15


def test_live_flushes_what_is_pending_on_sigterm(client, api, settings):
    _bootstrapped(client, api, settings)
    stop = threading.Event()
    clock = Clock()

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now - START > timedelta(seconds=90):
            stop.set()

    specs = registry.specs_from_api(client.get_lots())
    totals = run_live(
        Sender(client, sleeper=sleep),
        settings.model_copy(update={"flush_interval_s": 3600.0}),
        specs,
        scenario="normal",
        rate_multiplier=1.0,
        clock=clock,
        sleeper=sleep,
        stop=stop,
    )
    assert totals.received > 0 and totals.accepted == len(api.events)  # one flush, at shutdown


def test_live_keeps_batches_when_the_backend_is_down_and_bounds_the_outbox(settings):
    def down(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/lots":
            return httpx.Response(200, json=[])
        raise httpx.ConnectError("down")

    client = ParkingClient("http://t", transport=httpx.MockTransport(down))
    clock = Clock()
    specs = registry.desired_lots(TODAY)
    totals = run_live(
        Sender(client, sleeper=clock.sleep),
        settings.model_copy(update={"outbox_max": 3}),
        specs,
        scenario="rush_hour",
        rate_multiplier=1.0,
        duration_s=120,
        clock=clock,
        sleeper=clock.sleep,
    )
    assert totals.received == 0
    assert totals.dropped > 0 and totals.unsent <= 3


def test_heartbeat_and_healthcheck(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "hb"
    assert not heartbeat.is_fresh(path, 60)
    heartbeat.touch(path)
    assert heartbeat.is_fresh(path, 60)
    assert not heartbeat.is_fresh(path, 60, now=time.time() + 3600)
    monkeypatch.setenv("GENERATOR_HEARTBEAT_FILE", str(path))
    cli.get_settings.cache_clear()
    assert cli.main(["healthcheck"]) == 0
    path.unlink()
    assert cli.main(["healthcheck"]) == 1
    cli.get_settings.cache_clear()


def test_cli_parser():
    parser = cli.build_parser()
    args = parser.parse_args(["backfill", "--days", "7", "--end-date", "2026-10-01"])
    assert args.days == 7 and str(args.end_date) == "2026-10-01"
    args = parser.parse_args(["live", "--scenario", "rush_hour", "--rate", "2", "--duration", "30"])
    assert (args.scenario, args.rate, args.duration) == ("rush_hour", 2.0, 30.0)
    with pytest.raises(SystemExit):
        parser.parse_args(["nope"])


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, api: FakeApi, tmp_path):
    for name in [n for n in os.environ if n.startswith("GENERATOR_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("GENERATOR_ADMIN_API_KEY", ADMIN)
    monkeypatch.setenv("GENERATOR_INGEST_API_KEY", INGEST)
    monkeypatch.setenv("GENERATOR_STUDENTS", "200")
    monkeypatch.setenv("GENERATOR_HEARTBEAT_FILE", str(tmp_path / "hb"))
    cli.get_settings.cache_clear()
    real = ParkingClient.__init__

    def init(self, base_url, **kw):
        real(self, base_url, transport=api.transport(), **kw)

    monkeypatch.setattr(ParkingClient, "__init__", init)
    monkeypatch.setattr(cli, "_install_stop", lambda stop: None)
    yield
    cli.get_settings.cache_clear()


def test_cli_bootstrap_backfill_and_rerun(wired, api: FakeApi):
    assert cli.main(["bootstrap"]) == 0
    assert len(api.gates) == 4 and len(api.students) == 200
    assert cli.main(["backfill", "--days", "1"]) == 0
    count = len(api.events)
    assert count > 0
    assert cli.main(["backfill", "--days", "1"]) == 0
    assert len(api.events) == count


def test_cli_run_does_bootstrap_backfill_and_live(wired, api: FakeApi, monkeypatch):
    monkeypatch.setenv("GENERATOR_BACKFILL_DAYS", "1")
    cli.get_settings.cache_clear()
    monkeypatch.setattr(cli, "_sleeper", lambda stop: lambda s: stop.set())  # live stops at once
    assert cli.main(["run"]) == 0
    assert api.lots and api.events


def test_cli_exit_codes(wired, monkeypatch):
    monkeypatch.setenv("GENERATOR_ADMIN_API_KEY", "x" * 32)
    cli.get_settings.cache_clear()
    assert cli.main(["bootstrap"]) == 2  # 401 is fatal
    monkeypatch.delenv("GENERATOR_INGEST_API_KEY")
    cli.get_settings.cache_clear()
    with pytest.raises(SystemExit, match="INGEST_API_KEY"):
        cli.main(["backfill", "--days", "1"])


def test_cli_backfill_without_bootstrap_fails(wired):
    assert cli.main(["backfill", "--days", "1"]) == 1


def test_json_log_lines(caplog):
    import json
    import logging

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("w",), None)
    record.accepted = 3
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "hello w" and line["accepted"] == 3 and line["level"] == "INFO"


def test_settings_hide_keys(settings: Settings):
    shown = repr(Settings(_env_file=None, ingest_api_key=INGEST))
    assert INGEST not in shown
