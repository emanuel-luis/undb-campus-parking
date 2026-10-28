"""Time helpers. The service date is the local calendar day in the configured time zone."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def get_now() -> datetime:
    """FastAPI dependency: the current instant, timezone-aware UTC (override in tests)."""
    return datetime.now(UTC)


def service_date(ts: datetime, tz: ZoneInfo) -> date:
    """Local calendar date of an aware timestamp."""
    return ts.astimezone(tz).date()


def day_cutoff(d: date, tz: ZoneInfo) -> datetime:
    """Start of the next local day after `d`, as aware UTC."""
    next_midnight = datetime.combine(d + timedelta(days=1), time.min, tzinfo=tz)
    return next_midnight.astimezone(UTC)
