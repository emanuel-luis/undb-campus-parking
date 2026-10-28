"""Hypothesis properties 1-6 and 8-10 of the plan, against a pure in-memory model.

The model folds batches the way the ingest transaction does: dedupe by event id, re-derive the
stays of every touched (lot, student, service day) with `pair_stays`, add the change in open
stays to the lot's counter. Properties 1 and 8 are repeated against the real service in
test_ingestion_properties.py. Property 7 (close_day) belongs to a later PR.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from campus_parking.core.clock import service_date
from campus_parking.ingestion.validation import KnownGate, Reject, validate_item
from campus_parking.occupancy.domain import lot_figures
from campus_parking.occupancy.pairing import StayEvent, StayRecord, pair_stays

TZ = ZoneInfo("America/Fortaleza")
BASE = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)
LOTS = ["lot-a", "lot-b"]
STUDENTS = [f"stu_00000000000{i}" for i in range(4)]
Key = tuple[str, str, date]
PROFILE = settings(max_examples=200, deadline=None)


@dataclass(frozen=True)
class Ev:
    event_id: UUID
    lot: str
    student: str
    direction: str
    at: datetime

    @property
    def key(self) -> Key:
        return self.lot, self.student, service_date(self.at, TZ)

    @property
    def stay_event(self) -> StayEvent:
        return StayEvent(self.event_id, self.direction, self.at)


@dataclass
class Model:
    events: dict[UUID, Ev] = field(default_factory=dict)
    stays: dict[Key, list[StayRecord]] = field(default_factory=dict)
    occupied: Counter[str] = field(default_factory=Counter)

    def ingest(self, batch: list[Ev]) -> Counter[str]:
        """Apply one batch; returns the change in open stays per lot."""
        new = {e.event_id: e for e in batch if e.event_id not in self.events}
        self.events.update(new)
        deltas: Counter[str] = Counter()
        for key in {e.key for e in new.values()}:
            before = sum(s.status == "open" for s in self.stays.get(key, []))
            mine = [e.stay_event for e in self.events.values() if e.key == key]
            self.stays[key] = pair_stays(mine, False, None)
            deltas[key[0]] += sum(s.status == "open" for s in self.stays[key]) - before
        self.occupied.update(deltas)
        return deltas

    def snapshot(self) -> tuple[Any, Any]:
        return (
            {k: sorted(v, key=lambda s: s.stay_id) for k, v in self.stays.items() if v},
            {lot: n for lot, n in self.occupied.items() if n},
        )


def make_event(n: int, lot: str, student: str, direction: str, minutes: int) -> Ev:
    return Ev(UUID(int=n), lot, student, direction, BASE - timedelta(minutes=minutes))


@st.composite
def scenario(draw: st.DrawFn) -> tuple[list[Ev], list[list[Ev]]]:
    """Unique events, and one arrival: shuffled, with resent copies, split into batches."""
    specs = draw(
        st.lists(
            st.tuples(
                st.sampled_from(LOTS),
                st.sampled_from(STUDENTS),
                st.sampled_from(["entry", "exit"]),
                st.integers(0, 3000),  # about two days, so service days change
            ),
            max_size=24,
        )
    )
    events = [make_event(i + 1, *spec) for i, spec in enumerate(specs)]
    resent = draw(st.lists(st.sampled_from(events), max_size=8)) if events else []
    arrival = draw(st.permutations(events + resent))
    sizes = draw(st.lists(st.integers(1, 6), min_size=1, max_size=30))
    batches, start, i = [], 0, 0
    while start < len(arrival):
        size = sizes[i % len(sizes)]
        batches.append(list(arrival[start : start + size]))
        start, i = start + size, i + 1
    return events, batches


def run(batches: list[list[Ev]]) -> Model:
    model = Model()
    for batch in batches:
        model.ingest(batch)
    return model


def lww_open(events: list[Ev], until: datetime | None = None) -> Counter[str]:
    """Open stays per lot by last-write-wins: the latest event of each student-day is an entry."""
    latest: dict[Key, Ev] = {}
    for e in events:
        if until is not None and e.at > until:
            continue
        if e.key not in latest or (e.at, e.event_id) > (latest[e.key].at, latest[e.key].event_id):
            latest[e.key] = e
    return Counter(k[0] for k, e in latest.items() if e.direction == "entry")


@PROFILE
@given(scenario())
def test_1_arrival_order_duplicates_and_splits_do_not_matter(
    data: tuple[list[Ev], list[list[Ev]]],
) -> None:
    events, batches = data
    reference = run([sorted(events, key=lambda e: (e.at, e.event_id))])
    assert run(batches).snapshot() == reference.snapshot()


@PROFILE
@given(scenario())
def test_2_deltas_sum_to_the_open_count_and_to_last_write_wins(
    data: tuple[list[Ev], list[list[Ev]]],
) -> None:
    events, batches = data
    model, total = Model(), Counter[str]()
    for batch in batches:
        total.update(model.ingest(batch))
    open_count = Counter(
        {
            lot: sum(s.status == "open" for k, v in model.stays.items() if k[0] == lot for s in v)
            for lot in LOTS
        }
    )
    lww = lww_open(events)
    for lot in LOTS:
        assert total[lot] == model.occupied[lot] == open_count[lot] == lww[lot]


@PROFILE
@given(scenario())
def test_3_occupancy_is_never_negative(data: tuple[list[Ev], list[list[Ev]]]) -> None:
    _, batches = data
    model = Model()
    for batch in batches:
        model.ingest(batch)
        assert all(n >= 0 for n in model.occupied.values())


@PROFILE
@given(scenario())
def test_4_every_event_is_in_exactly_one_stay(data: tuple[list[Ev], list[list[Ev]]]) -> None:
    events, batches = data
    stays = [s for v in run(batches).stays.values() for s in v]
    used = [i for s in stays for i in (s.entry_event_id, s.exit_event_id) if i is not None]
    assert sorted(used) == sorted(e.event_id for e in events)
    by_status = Counter(s.status for s in stays)
    entries = sum(e.direction == "entry" for e in events)
    exits = len(events) - entries
    assert entries == (
        by_status["closed"]
        + by_status["missing_exit"]
        + by_status["open"]
        + by_status["closed_by_reconciliation"]
    )
    assert exits == by_status["closed"] + by_status["exit_only"]


@PROFILE
@given(scenario())
def test_5_stays_of_a_student_day_do_not_overlap(data: tuple[list[Ev], list[list[Ev]]]) -> None:
    _, batches = data
    for stays in run(batches).stays.values():
        previous_end: datetime | None = None
        for s in stays:  # pair_stays returns them in time order
            start = s.entered_at or s.exited_at
            assert start is not None
            if previous_end is not None:
                assert start >= previous_end
            if s.duration_s is not None:
                assert s.duration_s >= 0
            if s.ended_at is not None:
                assert s.ended_at >= start
                previous_end = s.ended_at
            else:
                previous_end = datetime.max.replace(tzinfo=UTC)


@PROFILE
@given(scenario(), st.integers(0, 3100))
def test_6_stay_intervals_agree_with_last_write_wins_at_any_time(
    data: tuple[list[Ev], list[list[Ev]]], minutes: int
) -> None:
    events, batches = data
    t = BASE - timedelta(minutes=minutes)
    from_stays: Counter[str] = Counter()
    for (lot, _, _), stays in run(batches).stays.items():
        for s in stays:
            if (
                s.entered_at is not None
                and s.entered_at <= t
                and (s.ended_at is None or s.ended_at > t)
            ):
                from_stays[lot] += 1
    assert +from_stays == +lww_open(events, until=t)


@PROFILE
@given(scenario(), st.data())
def test_8_resending_any_subset_changes_nothing(
    data: tuple[list[Ev], list[list[Ev]]], draw: st.DataObject
) -> None:
    events, batches = data
    model = run(batches)
    before = model.snapshot()
    subset = draw.draw(st.lists(st.sampled_from(events), max_size=10)) if events else []
    assert +model.ingest(subset) == Counter()
    assert model.snapshot() == before


@PROFILE
@given(st.none() | st.integers(1, 500), st.integers(0, 2000))
def test_9_free_stays_within_zero_and_capacity(capacity: int | None, occupied: int) -> None:
    free, pct, over = lot_figures(capacity, occupied)
    if capacity is None:
        assert (free, pct, over) == (None, None, False)
        return
    assert free is not None
    assert 0 <= free <= capacity
    assert over is (occupied > capacity)
    assert pct == round(occupied / capacity * 100, 1)


GATES = {"lot-a-in": KnownGate("lot-a", "entry"), "lot-a-out": KnownGate("lot-a", "exit")}
KNOWN = {STUDENTS[0], STUDENTS[1]}
ITEM = {
    "schema_version": 1,
    "event_id": str(UUID(int=1)),
    "gate_id": "lot-a-in",
    "lot_id": "lot-a",
    "direction": "entry",
    "student_ref": STUDENTS[0],
    "occurred_at": "2026-10-28T13:50:00Z",
}


@st.composite
def raw_items(draw: st.DrawFn) -> list[Any]:
    """Valid items plus one broken field each, so every reason shows up."""
    breaks: list[dict[str, Any]] = [
        {},
        {"gate_id": "ghost-gate"},
        {"student_ref": STUDENTS[3]},
        {"lot_id": "lot-b"},
        {"direction": "exit"},
        {"occurred_at": "2026-10-28T15:00:00Z"},
        {"occurred_at": "2025-01-01T00:00:00Z"},
        {"schema_version": 9},
    ]
    items: list[Any] = [
        {**ITEM, "event_id": str(UUID(int=i + 1)), **draw(st.sampled_from(breaks))}
        for i in range(draw(st.integers(1, 12)))
    ]
    return [*items, None, 5, []]


def outcome(item: Any) -> str:
    result = validate_item(item, GATES, KNOWN, BASE, 300, 120)
    return result.reason if isinstance(result, Reject) else "ok"


@PROFILE
@given(raw_items(), st.randoms(use_true_random=False))
def test_10_validation_does_not_depend_on_position(items: list[Any], rnd: Any) -> None:
    expected = [outcome(i) for i in items]
    order = list(range(len(items)))
    rnd.shuffle(order)
    shuffled = [outcome(items[i]) for i in order]
    assert shuffled == [expected[i] for i in order]
    assert set(expected) - {"ok"} <= {
        "unknown_gate",
        "unknown_student",
        "lot_mismatch",
        "direction_mismatch",
        "in_future",
        "too_old",
        "schema_invalid",
    }


def test_the_strategies_reach_the_interesting_cases() -> None:
    """Guard against a vacuous suite: a fixed example with every status."""

    def entry(n: int, m: int, s: str = STUDENTS[0]) -> Ev:
        return make_event(n, "lot-a", s, "entry", m)

    def exit_(n: int, m: int, s: str = STUDENTS[0]) -> Ev:
        return make_event(n, "lot-a", s, "exit", m)

    model = run(
        [[entry(1, 100), entry(2, 90), exit_(3, 80), entry(4, 70), exit_(5, 60, STUDENTS[1])]]
    )
    statuses = Counter(s.status for v in model.stays.values() for s in v)
    assert statuses == Counter(missing_exit=1, closed=1, open=1, exit_only=1)
    assert model.occupied == Counter({"lot-a": 1})
