"""Command line entry point: turnstile-gen bootstrap | backfill | live | run | healthcheck."""

import argparse
import logging
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from types import FrameType

import httpx
from pydantic import SecretStr

from turnstile_generator import heartbeat, registry
from turnstile_generator.backfill import BackfillError, backfill
from turnstile_generator.bootstrap import bootstrap
from turnstile_generator.client import ApiError, FatalApiError, ParkingClient
from turnstile_generator.live import run_live
from turnstile_generator.logging import configure_logging
from turnstile_generator.sender import GiveUpError, Sender, Totals, call_with_retry
from turnstile_generator.settings import Settings, get_settings

log = logging.getLogger("turnstile_generator.cli")
DEFAULT_BACKFILL_DAYS = 90
EXIT_FAILED, EXIT_FATAL = 1, 2


def _need(key: SecretStr | None, name: str) -> SecretStr:
    if key is None:
        raise SystemExit(f"GENERATOR_{name} is not set")
    return key


def _sleeper(stop: threading.Event) -> Callable[[float], None]:
    """Sleep that wakes early on SIGTERM, so shutdown never waits out a backoff."""

    def sleep(seconds: float) -> None:
        stop.wait(seconds)

    return sleep


def _today(settings: Settings) -> date:
    return datetime.now(UTC).astimezone(settings.tz).date()


def _client(settings: Settings, *, admin: bool = False, ingest: bool = False) -> ParkingClient:
    return ParkingClient(
        settings.api_url,
        ingest_key=_need(settings.ingest_api_key, "INGEST_API_KEY") if ingest else None,
        admin_key=_need(settings.admin_api_key, "ADMIN_API_KEY") if admin else None,
    )


def _install_stop(stop: threading.Event) -> None:
    def handler(signum: int, frame: FrameType | None) -> None:
        log.info("signal received, finishing the current batch", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _log_totals(name: str, totals: Totals) -> None:
    log.info(
        f"{name} finished",
        extra={
            "received": totals.received,
            "accepted": totals.accepted,
            "duplicates": totals.duplicates,
            "rejected": totals.rejected,
            "rejects_by_reason": dict(totals.rejects_by_reason),
        },
    )


def _bootstrap(settings: Settings, stop: threading.Event) -> None:
    client = _client(settings, admin=True)
    try:
        bootstrap(
            client,
            seed=settings.seed,
            students=settings.students,
            today=_today(settings),
            sleeper=_sleeper(stop),
        )
    finally:
        client.close()


def _backfill(settings: Settings, stop: threading.Event, days: int, end: date | None) -> None:
    client = _client(settings, ingest=True)
    try:
        today = _today(settings)
        totals = backfill(
            client,
            Sender(client, sleeper=_sleeper(stop)),
            settings,
            days=days,
            end_date=end or today - timedelta(days=1),
            today=today,
            stop=stop,
            on_batch=lambda: heartbeat.touch(settings.heartbeat_file),
        )
        _log_totals("backfill", totals)
    finally:
        client.close()


def _live(
    settings: Settings,
    stop: threading.Event,
    scenario: str | None,
    rate: float | None,
    duration: float | None,
) -> None:
    client = _client(settings, ingest=True)
    try:
        specs = registry.specs_from_api(call_with_retry(client.get_lots, sleeper=_sleeper(stop)))
        totals = run_live(
            Sender(client, sleeper=_sleeper(stop)),
            settings,
            specs,
            scenario=scenario or settings.scenario,  # type: ignore[arg-type]
            rate_multiplier=rate or settings.rate_multiplier,
            duration_s=duration,
            stop=stop,
            sleeper=_sleeper(stop),
            on_tick=lambda: heartbeat.touch(settings.heartbeat_file),
        )
        _log_totals("live", totals)
    finally:
        client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="turnstile-gen", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap", help="create lots, gates and students (idempotent)")
    bf = sub.add_parser("backfill", help="post past days of events")
    bf.add_argument(
        "--days", type=int, help=f"default GENERATOR_BACKFILL_DAYS or {DEFAULT_BACKFILL_DAYS}"
    )
    bf.add_argument("--end-date", type=date.fromisoformat, help="last day, default yesterday")
    for name, help_ in (
        ("live", "stream events in real time"),
        ("run", "bootstrap, backfill, live"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--scenario", choices=["normal", "rush_hour", "quiet"])
        p.add_argument("--rate", type=float, help="rate multiplier")
        p.add_argument("--duration", type=float, help="seconds; default runs until stopped")
    sub.add_parser("healthcheck", help="exit 0 if the heartbeat file is fresh")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if args.command == "healthcheck":
        return 0 if heartbeat.is_fresh(settings.heartbeat_file, settings.heartbeat_max_age_s) else 1
    configure_logging(settings.log_level)
    stop = threading.Event()
    _install_stop(stop)
    try:
        if args.command == "bootstrap":
            _bootstrap(settings, stop)
        elif args.command == "backfill":
            _backfill(
                settings,
                stop,
                args.days or settings.backfill_days or DEFAULT_BACKFILL_DAYS,
                args.end_date,
            )
        elif args.command == "live":
            _live(settings, stop, args.scenario, args.rate, args.duration)
        else:  # run
            _bootstrap(settings, stop)
            if settings.backfill_days > 0 and not stop.is_set():
                _backfill(settings, stop, settings.backfill_days, None)
            if not stop.is_set():
                _live(settings, stop, args.scenario, args.rate, args.duration)
    except FatalApiError as exc:
        log.error("fatal API error, not retrying", extra={"status": exc.status, "error": str(exc)})
        return EXIT_FATAL
    except (GiveUpError, BackfillError, ApiError, httpx.TransportError) as exc:
        log.error("failed", extra={"error": str(exc)})
        return EXIT_FAILED
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
