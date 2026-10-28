from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from campus_parking.core.clock import day_cutoff, get_now, service_date

TZ = ZoneInfo("America/Fortaleza")  # UTC-3, no DST


def test_get_now_is_aware_utc() -> None:
    now = get_now()
    assert now.tzinfo is UTC
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)


def test_service_date_uses_local_day() -> None:
    # 02:30 UTC is still the previous evening in Fortaleza
    assert service_date(datetime(2026, 10, 28, 2, 30, tzinfo=UTC), TZ) == date(2026, 10, 27)
    assert service_date(datetime(2026, 10, 28, 3, 0, tzinfo=UTC), TZ) == date(2026, 10, 28)


def test_service_date_accepts_other_offsets() -> None:
    ts = datetime(2026, 10, 28, 1, 0, tzinfo=timezone(timedelta(hours=-3)))
    assert service_date(ts, TZ) == date(2026, 10, 28)


def test_day_cutoff_is_next_local_midnight_in_utc() -> None:
    assert day_cutoff(date(2026, 10, 27), TZ) == datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
    assert day_cutoff(date(2026, 12, 31), TZ).tzinfo is UTC
