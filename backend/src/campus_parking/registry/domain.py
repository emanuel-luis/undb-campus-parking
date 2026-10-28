"""Pure registry rules."""

from collections.abc import Iterable
from datetime import date

from campus_parking.registry.schemas import CapacityPeriod


def current_capacity(history: Iterable[CapacityPeriod], today: date) -> int | None:
    """Capacity of the period with the latest valid_from <= today, or None if none has started."""
    started = [p for p in history if p.valid_from <= today]
    return max(started, key=lambda p: p.valid_from).capacity if started else None
