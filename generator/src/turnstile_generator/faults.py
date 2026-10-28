"""Seeded fault injector: the mess a real turnstile system sends."""

import random
from dataclasses import dataclass

from turnstile_generator.api_models import GateEvent
from turnstile_generator.settings import Settings
from turnstile_generator.stream import make_event

UNKNOWN_GATE = "unknown-gate"


@dataclass(frozen=True)
class FaultRates:
    duplicate: float = 0.0
    out_of_order: float = 0.0
    missing_exit: float = 0.0
    missing_entry: float = 0.0
    unknown_student: float = 0.0
    unknown_gate: float = 0.0

    @classmethod
    def from_settings(cls, s: Settings) -> "FaultRates":
        return cls(
            s.fault_duplicate_rate,
            s.fault_out_of_order_rate,
            s.fault_missing_exit_rate,
            s.fault_missing_entry_rate,
            s.fault_unknown_student_rate,
            s.fault_unknown_gate_rate,
        )


class FaultInjector:
    def __init__(self, rates: FaultRates, rng: random.Random) -> None:
        self.rates = rates
        self.rng = rng

    def mutate(self, event: GateEvent) -> list[GateEvent]:
        """The events actually sent for one real event: none, one (maybe corrupted) or a pair."""
        r, rng = self.rates, self.rng
        if event.direction == "exit" and rng.random() < r.missing_exit:
            return []
        if event.direction == "entry" and rng.random() < r.missing_entry:
            return []
        if rng.random() < r.unknown_student:
            ghost = f"stu_{rng.getrandbits(48):012x}"  # valid shape, never registered
            event = make_event(
                event.lot_id, event.gate_id, event.direction, ghost, event.occurred_at
            )
        elif rng.random() < r.unknown_gate:
            event = make_event(
                event.lot_id, UNKNOWN_GATE, event.direction, event.student_ref, event.occurred_at
            )
        return [event, event] if rng.random() < r.duplicate else [event]

    def held_back(self) -> bool:
        """Whether this event should be delivered later than its neighbours."""
        return self.rng.random() < self.rates.out_of_order

    def apply(self, events: list[GateEvent]) -> list[GateEvent]:
        """Backfill: mutate a sorted day and move some events later in the stream."""
        keyed: list[tuple[int, int, GateEvent]] = []
        for i, event in enumerate(events):
            for sent in self.mutate(event):
                shift = self.rng.randint(5, 300) if self.held_back() else 0
                keyed.append((i + shift, len(keyed), sent))
        keyed.sort(key=lambda k: (k[0], k[1]))
        return [e for _, _, e in keyed]
