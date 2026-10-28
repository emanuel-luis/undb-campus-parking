from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from campus_parking.jobs.scheduler import daily_due

TZ = ZoneInfo("America/Fortaleza")  # UTC-3, no DST
RUN_AT = time(0, 30)


def utc(hour: int, minute: int, day: int = 29) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def test_not_due_before_the_run_time() -> None:
    assert daily_due(utc(3, 29), TZ, RUN_AT, set()) is None  # 00:29 local


def test_due_for_yesterday_from_the_run_time_on() -> None:
    assert daily_due(utc(3, 30), TZ, RUN_AT, set()) == date(2026, 10, 28)
    assert daily_due(utc(20, 0), TZ, RUN_AT, set()) == date(2026, 10, 28)


def test_not_due_when_a_daily_already_exists_for_that_date() -> None:
    assert daily_due(utc(4, 0), TZ, RUN_AT, {date(2026, 10, 28)}) is None
    assert daily_due(utc(4, 0), TZ, RUN_AT, {date(2026, 10, 27)}) == date(2026, 10, 28)


def test_the_local_date_decides_not_the_utc_date() -> None:
    # 01:00 UTC on the 29th is 22:00 on the 28th locally: yesterday is the 27th, not the 28th.
    assert daily_due(utc(1, 0), TZ, RUN_AT, set()) == date(2026, 10, 27)
    # 03:30 UTC on the 30th is 00:30 on the 30th locally: yesterday is the 29th.
    assert daily_due(utc(3, 30, day=30), TZ, RUN_AT, set()) == date(2026, 10, 29)
