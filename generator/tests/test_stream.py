import random
import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from turnstile_generator import demand, registry, stream
from turnstile_generator.settings import Settings

TZ = ZoneInfo("America/Fortaleza")
SEMESTER = date(2026, 8, 3)
CHANGE = date(2026, 9, 13)


def _specs():
    specs = registry.desired_lots(CHANGE)
    pools = registry.split_students(registry.student_refs(42, 2000), specs)
    return specs, pools


def _day(spec, pools, d, seed=42, **kw):
    return stream.day_events(
        spec, pools[spec.lot_id], d, seed=seed, tz=TZ, semester_start=SEMESTER, **kw
    )


def _peak(events):
    occupied = peak = 0
    for e in events:  # sorted: exits before entries at the same instant
        occupied += 1 if e.direction == "entry" else -1
        assert occupied >= 0
        peak = max(peak, occupied)
    return occupied, peak


def test_stream_is_deterministic_per_seed_and_date():
    specs, pools = _specs()
    d = date(2026, 10, 26)
    first = _day(specs[0], pools, d)
    assert first == _day(specs[0], pools, d)
    assert first != _day(specs[0], pools, d, seed=43)
    assert first != _day(specs[0], pools, date(2026, 10, 27))


def test_event_id_is_a_stable_uuid5():
    at = datetime(2026, 10, 28, 11, 2, 13, 412000, tzinfo=UTC)
    event = stream.make_event("principal", "principal-entrada", "entry", "stu_8f3a2c1d9e04", at)
    expected = uuid.uuid5(stream.NAMESPACE, "stu_8f3a2c1d9e04|principal-entrada|" + at.isoformat())
    assert event.event_id == expected
    assert str(event.event_id) == "9fda283e-27a0-54b6-b2ee-62eebc10ed6b"
    local = at.astimezone(TZ)  # the same instant in another zone gets the same id
    assert (
        stream.make_event("principal", "principal-entrada", "entry", "stu_8f3a2c1d9e04", local)
        == event
    )


def test_capacity_is_never_exceeded_and_full_lots_turn_cars_away():
    specs, pools = _specs()
    principal = specs[0]
    events = _day(principal, pools, date(2026, 10, 26))  # a Monday: demand above capacity
    occupied, peak = _peak(events)
    assert (occupied, peak) == (0, principal.capacity_on(date(2026, 10, 26)))
    sim = stream.LotSimulator(principal, pools["principal"], random.Random(1))
    for i in range(5):
        sim.arrive(datetime(2026, 10, 26, 10, i, tzinfo=TZ), capacity=3)
    assert sim.occupied == 3 and sim.turned_away == 2


def test_capacity_follows_the_history():
    anexo = _specs()[0][1]
    assert anexo.capacity_on(CHANGE) == 150
    assert anexo.capacity_on(date(2026, 9, 12)) == 120
    assert anexo.capacity_on(date(2020, 1, 1)) == 120


def test_events_are_valid_and_inside_the_local_day():
    specs, pools = _specs()
    d = date(2026, 10, 27)
    for spec in specs:
        for e in _day(spec, pools, d):
            assert e.lot_id == spec.lot_id
            assert e.gate_id == (spec.entry_gate if e.direction == "entry" else spec.exit_gate)
            assert e.occurred_at.astimezone(TZ).date() == d


def test_weekend_and_holiday_volume_is_lower():
    specs, pools = _specs()
    counts = {
        d: len(_day(specs[0], pools, d))
        for d in (date(2026, 10, 26), date(2026, 10, 24), date(2026, 10, 25), date(2026, 11, 2))
    }
    monday, saturday, sunday, holiday = counts.values()
    assert saturday < 0.6 * monday and sunday < 0.2 * monday and holiday < 0.3 * monday


def test_demand_profile_shape():
    assert abs(sum(demand.MINUTE_SHARE) - 1) < 1e-9
    share = demand.MINUTE_SHARE
    hour = lambda h: sum(share[h * 60 : h * 60 + 60])
    assert hour(7) > 3 * hour(10) and hour(13) > 2 * hour(10) and hour(18) > 3 * hour(10)
    assert hour(2) < 0.001
    assert demand.day_factor(date(2026, 10, 24), SEMESTER) == 0.35
    assert demand.day_factor(date(2026, 10, 25), SEMESTER) == 0.05
    assert demand.day_factor(date(2026, 9, 7), SEMESTER) == 0.1
    assert demand.day_factor(date(2026, 10, 27), SEMESTER) == 1.0
    assert demand.day_factor(date(2026, 7, 29), SEMESTER) == 0.25  # quiet week, a Wednesday
    assert demand.day_factor(date(2026, 8, 5), SEMESTER) == 1.0


def test_scenarios_scale_the_rate():
    specs, pools = _specs()
    d = date(2026, 10, 27)
    normal = len(_day(specs[1], pools, d))
    assert len(_day(specs[1], pools, d, scenario="quiet")) < normal
    assert len(_day(specs[1], pools, d, rate_multiplier=0.2)) < normal


def test_students_do_not_overlap_within_a_lot():
    specs, pools = _specs()
    inside: set[str] = set()
    for e in _day(specs[0], pools, date(2026, 10, 26)):
        if e.direction == "entry":
            assert e.student_ref not in inside
            inside.add(e.student_ref)
        else:
            inside.remove(e.student_ref)


def test_settings_default_to_the_plan(settings: Settings):
    assert (
        settings.seed == 42 and settings.batch_size == 50 and settings.backfill_batch_size == 1000
    )
