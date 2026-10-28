"""Post a range of past days in large batches. Deterministic, so a rerun is all duplicates."""

import logging
import random
import threading
from collections.abc import Callable
from datetime import date, timedelta

from turnstile_generator import registry, stream
from turnstile_generator.api_models import GateEvent
from turnstile_generator.client import ParkingClient
from turnstile_generator.faults import FaultInjector, FaultRates
from turnstile_generator.sender import Sender, Totals
from turnstile_generator.settings import Settings

log = logging.getLogger("turnstile_generator.backfill")
MAX_DAYS = 118  # the backend rejects events older than 120 days (too_old)


class BackfillError(Exception):
    """The request cannot work: the registry is missing or the range is too old."""


def day_stream(
    settings: Settings, specs: list[registry.LotSpec], pools: dict[str, list[str]], d: date
) -> list[GateEvent]:
    """All lots' events for one local day, with the seeded faults applied."""
    events: list[GateEvent] = []
    for spec in specs:
        events += stream.day_events(
            spec,
            pools[spec.lot_id],
            d,
            seed=settings.seed,
            tz=settings.tz,
            semester_start=settings.semester_start,
            scenario=settings.scenario,
            rate_multiplier=settings.rate_multiplier,
        )
    events.sort(key=lambda e: e.occurred_at)
    rng = random.Random(f"{settings.seed}:faults:{d.isoformat()}")
    return FaultInjector(FaultRates.from_settings(settings), rng).apply(events)


def backfill(
    client: ParkingClient,
    sender: Sender,
    settings: Settings,
    *,
    days: int,
    end_date: date,
    today: date,
    stop: threading.Event | None = None,
    on_batch: Callable[[], None] = lambda: None,
) -> Totals:
    if days < 1:
        raise BackfillError("days must be at least 1")
    first = end_date - timedelta(days=days - 1)
    if (today - first).days > MAX_DAYS:
        raise BackfillError(f"{first} is older than the backend accepts (about {MAX_DAYS} days)")
    specs = registry.specs_from_api(client.get_lots())
    if not specs:
        raise BackfillError("the backend has no lots: run bootstrap first")
    pools = registry.split_students(registry.student_refs(settings.seed, settings.students), specs)
    total = Totals()
    for i in range(days):
        if stop is not None and stop.is_set():
            break
        d = first + timedelta(days=i)
        events = day_stream(settings, specs, pools, d)
        day = Totals()
        for start in range(0, len(events), settings.backfill_batch_size):
            day.add(sender.send(events[start : start + settings.backfill_batch_size]))
            on_batch()
        log.info(
            "backfill day",
            extra={
                "service_date": d.isoformat(),
                "sent": len(events),
                "accepted": day.accepted,
                "duplicates": day.duplicates,
                "rejected": day.rejected,
                "rejects_by_reason": dict(day.rejects_by_reason),
            },
        )
        total.merge(day)
    return total
