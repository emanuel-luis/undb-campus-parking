"""Live mode: real local time, arrivals drawn minute by minute, delivery every few seconds."""

import heapq
import logging
import random
import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from turnstile_generator import registry
from turnstile_generator.api_models import GateEvent
from turnstile_generator.faults import FaultInjector, FaultRates
from turnstile_generator.scenarios import Scenario
from turnstile_generator.sender import GiveUpError, Outbox, Sender, Totals
from turnstile_generator.settings import Settings
from turnstile_generator.stream import LotSimulator, minute_rate, poisson

log = logging.getLogger("turnstile_generator.live")
HOLD_BACK_S = (30.0, 300.0)  # out-of-order events are delayed by a random time in this range
SHUTDOWN_ATTEMPTS = 3


def utc_now() -> datetime:
    return datetime.now(UTC)


def run_live(
    sender: Sender,
    settings: Settings,
    specs: list[registry.LotSpec],
    *,
    scenario: Scenario,
    rate_multiplier: float,
    duration_s: float | None = None,
    clock: Callable[[], datetime] = utc_now,
    sleeper: Callable[[float], None] = time.sleep,
    stop: threading.Event | None = None,
    on_tick: Callable[[], None] = lambda: None,
    tick_s: float = 1.0,
) -> Totals:
    stop = stop or threading.Event()
    tz = settings.tz
    start = clock()
    deadline = None if duration_s is None else start + timedelta(seconds=duration_s)
    pools = registry.split_students(registry.student_refs(settings.seed, settings.students), specs)
    rng = random.Random(f"{settings.seed}:live:{start.isoformat()}")
    sims = {s.lot_id: LotSimulator(s, pools[s.lot_id], rng) for s in specs}
    injector = FaultInjector(FaultRates.from_settings(settings), rng)
    outbox = Outbox(settings.outbox_max)
    pending: list[tuple[datetime, int, LotSimulator]] = []  # arrivals scheduled, not yet due
    held: list[tuple[datetime, int, GateEvent]] = []  # out-of-order events waiting to be sent
    seq = 0
    totals = Totals()
    minute_stats: Counter[str] = Counter()
    next_minute = start.replace(second=0, microsecond=0)
    last_summary = next_minute
    next_flush = start

    def emit(event: GateEvent, now: datetime) -> None:
        nonlocal seq
        minute_stats["generated"] += 1
        for sent in injector.mutate(event):
            if injector.held_back():
                seq += 1
                release = now + timedelta(seconds=rng.uniform(*HOLD_BACK_S))
                heapq.heappush(held, (release, seq, sent))
            else:
                outbox.push([sent])

    def flush(attempts: int | None = None) -> None:
        while len(outbox):
            batch = outbox.pop_batch(settings.batch_size)
            try:
                result = sender.send(batch) if attempts is None else sender.send(batch, attempts)
            except GiveUpError:
                outbox.push(batch)  # keep it for the next flush; the outbox bound drops the oldest
                log.warning(
                    "batch not delivered, kept in the outbox", extra={"outbox": len(outbox)}
                )
                return
            totals.add(result)
            minute_stats["accepted"] += result.accepted
            minute_stats["duplicates"] += result.duplicates
            minute_stats["rejected"] += result.rejected

    while not stop.is_set():
        now = clock()
        if deadline is not None and now >= deadline:
            break
        while next_minute <= now:
            local = next_minute.astimezone(tz)
            for spec in specs:
                rate = minute_rate(
                    spec,
                    local.date(),
                    local.hour * 60 + local.minute,
                    scenario=scenario,
                    rate_multiplier=rate_multiplier,
                    semester_start=settings.semester_start,
                )
                n = poisson(rng, max(rate, settings.min_rate_per_min / len(specs)))
                for offset in sorted(rng.randrange(60_000) for _ in range(n)):
                    seq += 1
                    at = next_minute + timedelta(milliseconds=offset)
                    heapq.heappush(pending, (at, seq, sims[spec.lot_id]))
            next_minute += timedelta(minutes=1)
        while pending and pending[0][0] <= now:
            at, _, sim = heapq.heappop(pending)
            for event in sim.advance(at):
                emit(event, now)
            capacity = sim.spec.capacity_on(at.astimezone(tz).date())
            if (entry := sim.arrive(at, capacity)) is not None:
                emit(entry, now)
        for sim in sims.values():
            for event in sim.advance(now):
                emit(event, now)
        while held and held[0][0] <= now:
            outbox.push([heapq.heappop(held)[2]])
        if now >= next_flush:
            flush()
            next_flush = now + timedelta(seconds=settings.flush_interval_s)
        if now - last_summary >= timedelta(minutes=1):
            log.info(
                "live minute",
                extra={
                    **minute_stats,
                    "outbox": len(outbox),
                    "dropped": outbox.dropped,
                    "occupied": {k: s.occupied for k, s in sims.items()},
                },
            )
            minute_stats.clear()
            last_summary = now
        on_tick()
        sleeper(tick_s)

    outbox.push([e for _, _, e in sorted(held)])
    flush(SHUTDOWN_ATTEMPTS)
    log.info("live stopped", extra={"outbox": len(outbox), "dropped": outbox.dropped})
    return totals
