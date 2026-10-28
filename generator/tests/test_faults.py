import random
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from turnstile_generator.faults import UNKNOWN_GATE, FaultInjector, FaultRates
from turnstile_generator.stream import make_event

N = 20_000


def _events(n: int = N):
    t0 = datetime(2026, 10, 27, 8, tzinfo=UTC)
    out = []
    for i in range(n):
        direction = "entry" if i % 2 == 0 else "exit"
        gate = "principal-entrada" if direction == "entry" else "principal-saida"
        ref = f"stu_{i:012x}"
        out.append(make_event("principal", gate, direction, ref, t0 + timedelta(seconds=i)))
    return out


def _inject(rates: FaultRates, seed: int = 1):
    events = _events()
    return events, FaultInjector(rates, random.Random(seed)).apply(events)


def test_no_faults_means_no_change():
    events, sent = _inject(FaultRates())
    assert sent == events


def _share(count: int, of: int) -> float:
    return count / of


def test_missing_exit_and_entry_rates():
    events, sent = _inject(FaultRates(missing_exit=0.05, missing_entry=0.1))
    kept = {e.event_id for e in sent}
    exits = [e for e in events if e.direction == "exit"]
    entries = [e for e in events if e.direction == "entry"]
    assert abs(_share(sum(e.event_id not in kept for e in exits), len(exits)) - 0.05) < 0.01
    assert abs(_share(sum(e.event_id not in kept for e in entries), len(entries)) - 0.1) < 0.01


def test_unknown_student_and_gate_rates():
    events, sent = _inject(FaultRates(unknown_student=0.03, unknown_gate=0.02))
    ids = {e.event_id for e in events}
    gate = sum(e.gate_id == UNKNOWN_GATE for e in sent)
    student = sum(e.event_id not in ids and e.gate_id != UNKNOWN_GATE for e in sent)
    assert abs(_share(student, N) - 0.03) < 0.006
    assert abs(_share(gate, N) - 0.02 * 0.97) < 0.006


def test_duplicate_rate_resends_identical_events():
    events, sent = _inject(FaultRates(duplicate=0.04))
    assert len(sent) - len(events) == len(sent) - len({e.event_id for e in sent})
    assert abs(_share(len(sent) - len(events), N) - 0.04) < 0.006


def test_out_of_order_moves_events_later():
    events, sent = _inject(FaultRates(out_of_order=0.1))
    assert sorted(sent, key=lambda e: e.occurred_at) == events  # same events, none lost
    inversions = sum(a.occurred_at > b.occurred_at for a, b in pairwise(sent))
    assert 0.05 * N < inversions < 0.2 * N


def test_faults_are_seeded():
    rates = FaultRates(0.05, 0.05, 0.05, 0.05, 0.05, 0.05)
    assert _inject(rates, 7)[1] == _inject(rates, 7)[1]
    assert _inject(rates, 7)[1] != _inject(rates, 8)[1]


def test_held_back_uses_the_rate():
    injector = FaultInjector(FaultRates(out_of_order=1.0), random.Random(0))
    assert injector.held_back()
