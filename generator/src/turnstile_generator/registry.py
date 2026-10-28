"""The desired registry: two lots, four gates and pseudonymous students."""

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta

from turnstile_generator.api_models import CapacityPeriod, LotOut

# Capacity history starts at a fixed date, so it never depends on the day of the first run.
HISTORY_START = date(2024, 1, 1)
ANEXO_CHANGE_DAYS_AGO = 45


@dataclass(frozen=True)
class LotSpec:
    lot_id: str
    name: str
    history: tuple[tuple[date, int], ...]  # (valid_from, capacity), sorted
    daily_arrivals: int  # arrivals on a plain weekday, before faults and turn-aways

    @property
    def entry_gate(self) -> str:
        return f"{self.lot_id}-entrada"

    @property
    def exit_gate(self) -> str:
        return f"{self.lot_id}-saida"

    def capacity_on(self, d: date) -> int:
        current = self.history[0][1]
        for valid_from, capacity in self.history:
            if valid_from <= d:
                current = capacity
        return current

    def capacity_periods(self) -> list[CapacityPeriod]:
        return [CapacityPeriod(valid_from=v, capacity=c) for v, c in self.history]


LOT_NAMES = {"principal": "Estacionamento Principal", "anexo": "Estacionamento Anexo"}
DAILY_ARRIVALS = {"principal": 700, "anexo": 450}


def anexo_change_date(existing: list[LotOut], today: date) -> date:
    """The date capacity rose to 150: read back from the API once bootstrapped, else today - 45."""
    for lot in existing:
        if lot.lot_id == "anexo":
            for period in lot.capacity_history:
                if period.capacity == 150:
                    return period.valid_from
    return today - timedelta(days=ANEXO_CHANGE_DAYS_AGO)


def desired_lots(change: date) -> list[LotSpec]:
    return [
        LotSpec(
            "principal",
            LOT_NAMES["principal"],
            ((HISTORY_START, 200),),
            DAILY_ARRIVALS["principal"],
        ),
        LotSpec(
            "anexo",
            LOT_NAMES["anexo"],
            ((HISTORY_START, 120), (change, 150)),
            DAILY_ARRIVALS["anexo"],
        ),
    ]


def specs_from_api(lots: list[LotOut]) -> list[LotSpec]:
    """Lot specs whose capacity history is what the backend holds (the truth for backfills)."""
    return [
        LotSpec(
            lot.lot_id,
            lot.name,
            tuple((p.valid_from, p.capacity) for p in lot.capacity_history),
            DAILY_ARRIVALS.get(lot.lot_id, 400),
        )
        for lot in lots
        if lot.lot_id in LOT_NAMES
    ]


def student_ref(seed: int, index: int) -> str:
    return "stu_" + hashlib.sha256(f"{seed}:{index}".encode()).hexdigest()[:12]


def student_refs(seed: int, count: int) -> list[str]:
    return [student_ref(seed, i) for i in range(count)]


def split_students(refs: list[str], specs: list[LotSpec]) -> dict[str, list[str]]:
    """Give every lot its own slice of the students, proportional to its daily arrivals."""
    total = sum(s.daily_arrivals for s in specs)
    pools: dict[str, list[str]] = {}
    start = 0
    for i, spec in enumerate(specs):
        end = len(refs) if i == len(specs) - 1 else start + len(refs) * spec.daily_arrivals // total
        pools[spec.lot_id] = refs[start:end]
        start = end
    return pools
