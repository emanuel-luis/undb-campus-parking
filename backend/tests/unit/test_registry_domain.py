from datetime import date

import pytest

from campus_parking.core.db import insert_ignore
from campus_parking.registry.domain import current_capacity
from campus_parking.registry.models import Student
from campus_parking.registry.schemas import CapacityPeriod

TODAY = date(2026, 10, 27)


def periods(*items: tuple[str, int]) -> list[CapacityPeriod]:
    return [CapacityPeriod(valid_from=date.fromisoformat(d), capacity=c) for d, c in items]


def test_current_is_latest_started_period() -> None:
    history = periods(("2026-01-01", 100), ("2026-10-01", 150), ("2027-01-01", 300))
    assert current_capacity(history, TODAY) == 150


def test_period_starting_today_counts() -> None:
    assert current_capacity(periods(("2026-10-27", 90), ("2026-01-01", 10)), TODAY) == 90


def test_unsorted_history_is_fine() -> None:
    assert current_capacity(periods(("2026-10-01", 150), ("2026-01-01", 100)), TODAY) == 150


def test_all_future_is_none() -> None:
    assert current_capacity(periods(("2026-10-28", 100)), TODAY) is None


def test_insert_ignore_dialects() -> None:
    assert "ON CONFLICT" in str(insert_ignore("sqlite", Student).values(student_ref="x"))
    assert "ON CONFLICT" in str(insert_ignore("postgresql", Student).values(student_ref="x"))
    with pytest.raises(NotImplementedError):
        insert_ignore("mysql", Student)
