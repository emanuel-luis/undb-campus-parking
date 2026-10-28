import random
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fake_api import INGEST, problem
from pydantic import SecretStr

from turnstile_generator.client import FatalApiError, ParkingClient
from turnstile_generator.sender import (
    BACKOFF_CAP_S,
    GiveUpError,
    Outbox,
    Sender,
    backoff_cap,
    call_with_retry,
)
from turnstile_generator.stream import make_event


def _event(i: int = 0):
    at = datetime(2026, 10, 27, 8, tzinfo=UTC)
    return make_event("principal", "principal-entrada", "entry", f"stu_{i:012x}", at)


def _ok(request: httpx.Request) -> httpx.Response:
    n = request.content.count(b'"event_id"')
    body = {
        "batch_id": str(uuid.uuid4()),
        "received": n,
        "accepted": n,
        "duplicates": 0,
        "rejected": 0,
        "rejects": [],
    }
    return httpx.Response(202, json=body)


def _sender(script, sleeps, seed=0):
    """A sender whose transport plays `script` (responses or exceptions), then answers 202."""
    calls = iter(script)

    def handler(request: httpx.Request) -> httpx.Response:
        step = next(calls, None)
        if step is None:
            return _ok(request)
        if isinstance(step, Exception):
            raise step
        return step

    client = ParkingClient(
        "http://t", ingest_key=SecretStr(INGEST), transport=httpx.MockTransport(handler)
    )
    return Sender(client, sleeper=sleeps.append, rng=random.Random(seed))


def test_success_without_retries():
    sleeps: list[float] = []
    result = _sender([], sleeps).send([_event()])
    assert result.accepted == 1 and sleeps == []


def test_429_waits_exactly_retry_after():
    sleeps: list[float] = []
    sender = _sender([problem(429, "ingest-busy", {"Retry-After": "1"})], sleeps)
    assert sender.send([_event()]).accepted == 1
    assert sleeps == [1.0]


def test_503_honors_retry_after_and_caps_it():
    sleeps: list[float] = []
    script = [
        problem(503, "service-draining", {"Retry-After": "5"}),
        problem(503, "database-unavailable", {"Retry-After": "9999"}),
    ]
    _sender(script, sleeps).send([_event()])
    assert sleeps == [5.0, 60.0]


def test_5xx_and_network_errors_back_off_with_jitter():
    sleeps: list[float] = []
    script = [
        problem(500, "internal-error"),
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
        problem(502, "x"),
    ]
    _sender(script, sleeps).send([_event()])
    assert len(sleeps) == 4
    assert all(0 <= s <= backoff_cap(n) for n, s in enumerate(sleeps, start=1))
    assert backoff_cap(1) == 0.5 and backoff_cap(2) == 1.0 and backoff_cap(20) == BACKOFF_CAP_S


@pytest.mark.parametrize("status", [401, 403, 422])
def test_client_errors_are_fatal_and_not_retried(status):
    sleeps: list[float] = []
    sender = _sender([problem(status, "nope")], sleeps)
    with pytest.raises(FatalApiError) as info:
        sender.send([_event()])
    assert info.value.status == status and "nope" in str(info.value) and sleeps == []


def test_gives_up_after_ten_attempts():
    sleeps: list[float] = []
    sender = _sender([httpx.ConnectError("down")] * 20, sleeps)
    with pytest.raises(GiveUpError):
        sender.send([_event()])
    assert len(sleeps) == 9


def _raise_down():
    raise httpx.ConnectError("down")


def test_max_attempts_can_be_lowered():
    sleeps: list[float] = []
    with pytest.raises(GiveUpError):
        call_with_retry(_raise_down, sleeper=sleeps.append, max_attempts=3)
    assert len(sleeps) == 2


def test_send_all_batches_and_totals():
    sender = _sender([], [])
    totals = sender.send_all([_event(i) for i in range(25)], 10)
    assert totals.received == 25 and totals.accepted == 25


def test_outbox_drops_oldest_when_full(caplog):
    box = Outbox(3)
    events = [_event(i) for i in range(5)]
    box.push(events)
    assert len(box) == 3 and box.dropped == 2
    assert box.pop_batch(10) == events[2:]
    assert "outbox full" in caplog.text
