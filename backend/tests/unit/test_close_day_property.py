"""Property 7 on the pure model of test_properties.py: close_day is idempotent. The SQL path is
covered by test_rerun_changes_nothing in tests/api/test_jobs.py."""

from collections import Counter
from datetime import date
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from campus_parking.core.clock import day_cutoff
from campus_parking.occupancy.pairing import pair_stays
from tests.unit.test_properties import PROFILE, TZ, Ev, Key, Model, make_event, run, scenario


def close_day(model: Model, day: date) -> Counter[str]:
    """What reconcile_day does to the model: re-derive the day's stays as closed and subtract the
    open ones from the counters. Returns the change per lot."""
    cutoff = day_cutoff(day, TZ)
    deltas: Counter[str] = Counter()
    for key in [k for k in model.stays if k[2] == day]:
        before = sum(s.status == "open" for s in model.stays[key])
        mine = [e.stay_event for e in model.events.values() if e.key == key]
        model.stays[key] = pair_stays(mine, True, cutoff)
        deltas[key[0]] += sum(s.status == "open" for s in model.stays[key]) - before
    model.occupied.update(deltas)
    return deltas


def days(model: Model) -> list[date]:
    return sorted({k[2] for k in model.stays})


@PROFILE
@given(scenario(), st.data())
def test_7_close_day_is_idempotent_and_leaves_no_open_stay(
    data: tuple[list[Ev], list[list[Ev]]], draw: st.DataObject
) -> None:
    _, batches = data
    model = run(batches)
    if not days(model):
        return
    day = draw.draw(st.sampled_from(days(model)))
    others: dict[Key, Any] = {k: v for k, v in model.stays.items() if k[2] != day}
    close_day(model, day)
    after_first = model.snapshot()
    assert all(s.status != "open" for k, v in model.stays.items() if k[2] == day for s in v)
    assert {k: v for k, v in model.stays.items() if k[2] != day} == others
    assert all(n >= 0 for n in model.occupied.values())

    assert +close_day(model, day) == Counter()  # a second close changes nothing
    assert model.snapshot() == after_first


def test_close_day_example() -> None:
    model = run([[make_event(1, "lot-a", "stu_000000000000", "entry", 10)]])
    assert model.occupied["lot-a"] == 1
    day = days(model)[0]
    assert close_day(model, day) == Counter({"lot-a": -1})
    assert model.occupied["lot-a"] == 0
    (stay,) = next(iter(model.stays.values()))
    assert stay.status == "closed_by_reconciliation"
