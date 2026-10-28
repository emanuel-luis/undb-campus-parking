"""Delivery with retries: full-jitter backoff, Retry-After, a bounded outbox for live mode."""

import logging
import random
import time
from collections import Counter, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import httpx

from turnstile_generator.api_models import GateEvent, IngestResult
from turnstile_generator.client import ParkingClient, RetryableApiError

log = logging.getLogger("turnstile_generator.sender")

BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 30.0
MAX_ATTEMPTS = 10


class GiveUpError(Exception):
    """A call kept failing with retryable errors until the attempts ran out."""


def backoff_cap(attempt: int) -> float:
    """Upper bound of the jittered wait after the n-th failed attempt (1-based)."""
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2.0 ** (attempt - 1))


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    sleeper: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> T:
    """Run `fn`, retrying network errors, 5xx and 429. Other 4xx (401, 403, 422) propagate."""
    rng = rng or random.Random()
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except RetryableApiError as exc:
            wait = exc.retry_after
            reason = f"HTTP {exc.status}"
        except httpx.TransportError as exc:
            wait, reason = None, type(exc).__name__
        if attempt == max_attempts:
            raise GiveUpError(f"gave up after {max_attempts} attempts ({reason})")
        if wait is None:
            wait = rng.uniform(0, backoff_cap(attempt))
        log.warning(
            "retrying", extra={"attempt": attempt, "reason": reason, "wait_s": round(wait, 2)}
        )
        sleeper(wait)
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass
class Totals:
    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    rejects_by_reason: Counter[str] = field(default_factory=Counter)

    def add(self, result: IngestResult) -> None:
        self.received += result.received
        self.accepted += result.accepted
        self.duplicates += result.duplicates
        self.rejected += result.rejected
        self.rejects_by_reason.update(r.reason for r in result.rejects)

    def merge(self, other: "Totals") -> None:
        self.received += other.received
        self.accepted += other.accepted
        self.duplicates += other.duplicates
        self.rejected += other.rejected
        self.rejects_by_reason.update(other.rejects_by_reason)


class Sender:
    def __init__(
        self,
        client: ParkingClient,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.client = client
        self.sleeper = sleeper
        self.rng = rng

    def send(self, events: Sequence[GateEvent], max_attempts: int = MAX_ATTEMPTS) -> IngestResult:
        return call_with_retry(
            lambda: self.client.post_events(events),
            sleeper=self.sleeper,
            rng=self.rng,
            max_attempts=max_attempts,
        )

    def send_all(self, events: Sequence[GateEvent], batch_size: int) -> Totals:
        totals = Totals()
        for i in range(0, len(events), batch_size):
            totals.add(self.send(events[i : i + batch_size]))
        return totals


class Outbox:
    """Bounded FIFO for live mode: when full, the oldest events are dropped with a warning."""

    def __init__(self, max_size: int) -> None:
        self.max_size = max_size
        self._items: deque[GateEvent] = deque()
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._items)

    def push(self, events: Sequence[GateEvent]) -> None:
        self._items.extend(events)
        over = len(self._items) - self.max_size
        if over > 0:
            for _ in range(over):
                self._items.popleft()
            self.dropped += over
            log.warning("outbox full, dropped oldest events", extra={"dropped": over})

    def pop_batch(self, n: int) -> list[GateEvent]:
        return [self._items.popleft() for _ in range(min(n, len(self._items)))]
