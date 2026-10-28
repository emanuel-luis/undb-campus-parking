from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from campus_parking.occupancy.pairing import StayEvent, StayRecord, pair_stays

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def ev(n: int, direction: str, minute: int) -> StayEvent:
    return StayEvent(UUID(int=n), direction, T0 + timedelta(minutes=minute))


def statuses(stays: list[StayRecord]) -> list[str]:
    return [s.status for s in stays]


def test_no_events_no_stays() -> None:
    assert pair_stays([], False, None) == []


def test_entry_then_exit_is_closed_with_duration() -> None:
    [stay] = pair_stays([ev(1, "entry", 0), ev(2, "exit", 90)], False, None)
    assert stay == StayRecord(
        UUID(int=1),
        "closed",
        UUID(int=1),
        UUID(int=2),
        T0,
        T0 + timedelta(minutes=90),
        T0 + timedelta(minutes=90),
        5400,
    )


def test_open_stay_has_no_end() -> None:
    [stay] = pair_stays([ev(1, "entry", 0)], False, None)
    assert (stay.status, stay.ended_at, stay.exited_at, stay.duration_s) == (
        "open",
        None,
        None,
        None,
    )


def test_exit_only_uses_the_exit_id_and_has_no_duration() -> None:
    [stay] = pair_stays([ev(2, "exit", 5)], False, None)
    assert stay.stay_id == UUID(int=2)
    assert (stay.status, stay.entry_event_id, stay.entered_at, stay.duration_s) == (
        "exit_only",
        None,
        None,
        None,
    )
    assert stay.ended_at == stay.exited_at == T0 + timedelta(minutes=5)


def test_double_entry_marks_the_first_as_missing_exit() -> None:
    first, second = pair_stays([ev(1, "entry", 0), ev(2, "entry", 30)], False, None)
    assert (first.status, first.ended_at, first.duration_s) == (
        "missing_exit",
        T0 + timedelta(minutes=30),
        None,
    )
    assert (second.status, second.stay_id) == ("open", UUID(int=2))


def test_entry_arriving_after_its_exit_turns_exit_only_into_closed() -> None:
    exit_ = ev(2, "exit", 60)
    assert statuses(pair_stays([exit_], False, None)) == ["exit_only"]
    assert statuses(pair_stays([exit_, ev(1, "entry", 0)], False, None)) == ["closed"]


def test_entry_exit_entry_is_closed_plus_open() -> None:
    stays = pair_stays([ev(3, "entry", 120), ev(2, "exit", 60), ev(1, "entry", 0)], False, None)
    assert statuses(stays) == ["closed", "open"]


def test_exit_then_entry_is_exit_only_plus_open() -> None:
    assert statuses(pair_stays([ev(1, "exit", 0), ev(2, "entry", 5)], False, None)) == [
        "exit_only",
        "open",
    ]


def test_ties_break_on_event_id() -> None:
    entry_first = pair_stays([ev(2, "exit", 0), ev(1, "entry", 0)], False, None)
    exit_first = pair_stays([ev(1, "exit", 0), ev(2, "entry", 0)], False, None)
    assert statuses(entry_first) == ["closed"]
    assert statuses(exit_first) == ["exit_only", "open"]


def test_a_closed_day_reconciles_the_open_stay_at_the_cutoff() -> None:
    cutoff = T0 + timedelta(hours=12)
    stays = pair_stays([ev(1, "entry", 0), ev(2, "exit", 10), ev(3, "entry", 20)], True, cutoff)
    assert statuses(stays) == ["closed", "closed_by_reconciliation"]
    assert stays[1].ended_at == cutoff
    assert stays[1].duration_s is None


def test_a_closed_day_needs_a_cutoff() -> None:
    with pytest.raises(ValueError, match="cutoff"):
        pair_stays([ev(1, "entry", 0)], True, None)
