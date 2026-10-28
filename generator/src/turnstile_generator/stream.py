"""Capacity-aware event stream: cars arrive, stay a plausible time and leave.

A lot simulation is deterministic for (seed, lot, date). A car that arrives at a full lot is turned
away: it produces no events, which is what creates minutes at full capacity.
"""

import heapq
import math
import random
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from turnstile_generator import demand, scenarios
from turnstile_generator.api_models import GateEvent
from turnstile_generator.registry import LotSpec

# Fixed project namespace: event ids stay stable across runs and machines.
NAMESPACE = uuid.UUID("5d0c9f7e-3b1a-4c52-9a67-1f0e8d2b7a44")


def make_event(
    lot_id: str, gate_id: str, direction: str, student_ref: str, occurred_at: datetime
) -> GateEvent:
    occurred_at = occurred_at.astimezone(UTC)
    event_id = uuid.uuid5(NAMESPACE, f"{student_ref}|{gate_id}|{occurred_at.isoformat()}")
    return GateEvent.model_validate(
        {
            "schema_version": 1,
            "event_id": str(event_id),
            "gate_id": gate_id,
            "lot_id": lot_id,
            "direction": direction,
            "student_ref": student_ref,
            "occurred_at": occurred_at,
        }
    )


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's method; the per-minute rates here are small."""
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def stay_minutes(rng: random.Random, arrival_hour: float) -> float:
    """Length of a stay by period of arrival: long mornings, shorter lunch and evening visits."""
    if arrival_hour < 10:
        mean, sd = 300.0, 90.0
    elif arrival_hour < 15:
        mean, sd = 180.0, 60.0
    else:
        mean, sd = 210.0, 60.0
    return min(max(rng.gauss(mean, sd), 10.0), 600.0)


class LotSimulator:
    """Simulated occupancy of one lot: arrivals in, scheduled exits out."""

    def __init__(self, spec: LotSpec, pool: list[str], rng: random.Random) -> None:
        self.spec = spec
        self.pool = pool
        self.rng = rng
        self.occupied = 0
        self.turned_away = 0
        self._exits: list[tuple[datetime, int, str]] = []  # (exit time, tiebreak, student)
        self._busy: set[str] = set()
        self._seq = 0

    def advance(self, now: datetime) -> list[GateEvent]:
        """Exit events of every car that leaves at or before `now`."""
        out = []
        while self._exits and self._exits[0][0] <= now:
            at, _, student = heapq.heappop(self._exits)
            self.occupied -= 1
            self._busy.discard(student)
            out.append(make_event(self.spec.lot_id, self.spec.exit_gate, "exit", student, at))
        return out

    def arrive(
        self, at: datetime, capacity: int, latest_exit: datetime | None = None
    ) -> GateEvent | None:
        """An entry event, or None when the lot is full or no free student is found."""
        if self.occupied >= capacity:
            self.turned_away += 1
            return None
        for _ in range(8):
            student = self.rng.choice(self.pool)
            if student not in self._busy:
                break
        else:
            return None
        leaves = at + timedelta(minutes=stay_minutes(self.rng, at.hour + at.minute / 60))
        if latest_exit is not None:
            leaves = min(leaves, latest_exit)
        self._seq += 1
        heapq.heappush(self._exits, (leaves, self._seq, student))
        self._busy.add(student)
        self.occupied += 1
        return make_event(self.spec.lot_id, self.spec.entry_gate, "entry", student, at)


def minute_rate(
    spec: LotSpec,
    d: date,
    minute: int,
    *,
    scenario: scenarios.Scenario,
    rate_multiplier: float,
    semester_start: date,
) -> float:
    """Expected arrivals at this lot during one minute of the local day."""
    return (
        spec.daily_arrivals
        * demand.day_factor(d, semester_start)
        * demand.MINUTE_SHARE[minute]
        * scenarios.multiplier(scenario, minute)
        * rate_multiplier
    )


def day_events(
    spec: LotSpec,
    pool: list[str],
    d: date,
    *,
    seed: int,
    tz: ZoneInfo,
    semester_start: date,
    scenario: scenarios.Scenario = "normal",
    rate_multiplier: float = 1.0,
) -> list[GateEvent]:
    """Every event of one lot on one local day, sorted by time. Deterministic for (seed, d)."""
    rng = random.Random(f"{seed}:{spec.lot_id}:{d.isoformat()}")
    sim = LotSimulator(spec, pool, rng)
    capacity = spec.capacity_on(d)
    start = datetime.combine(d, time.min, tzinfo=tz)
    last_exit = start + timedelta(minutes=1439)
    events: list[GateEvent] = []
    for minute in range(1440):
        rate = minute_rate(
            spec,
            d,
            minute,
            scenario=scenario,
            rate_multiplier=rate_multiplier,
            semester_start=semester_start,
        )
        n = poisson(rng, rate)
        for offset in sorted(rng.randrange(60_000) for _ in range(n)):
            at = start + timedelta(minutes=minute, milliseconds=offset)
            events += sim.advance(at)
            if (entry := sim.arrive(at, capacity, last_exit)) is not None:
                events.append(entry)
    events += sim.advance(start + timedelta(days=1))
    return sorted(events, key=lambda e: (e.occurred_at, e.direction != "exit", str(e.event_id)))
