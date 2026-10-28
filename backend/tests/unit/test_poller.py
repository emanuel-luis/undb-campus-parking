"""Poller change detection and failure handling, against a fake source."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from campus_parking.core import metrics
from campus_parking.occupancy.schemas import AvailabilitySnapshot, LotAvailability
from campus_parking.realtime.hub import Hub, Message
from campus_parking.realtime.poller import Poller

T0 = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)


def lot(lot_id: str, version: int, occupied: int = 0, capacity: int | None = 10) -> LotAvailability:
    return LotAvailability(
        lot_id=lot_id,
        name=lot_id,
        capacity=capacity,
        occupied=occupied,
        free=None if capacity is None else max(capacity - occupied, 0),
        occupancy_pct=None,
        over_capacity=False,
        version=version,
        updated_at=T0,
        last_event_at=None,
    )


class FakeSource:
    def __init__(self) -> None:
        self.lots = [lot("lot-a", 0), lot("lot-b", 0)]
        self.fail = False

    async def versions(self) -> dict[str, int]:
        if self.fail:
            raise OSError("database down")
        return {x.lot_id: x.version for x in self.lots}

    async def snapshot(self) -> AvailabilitySnapshot:
        return AvailabilitySnapshot(generated_at=T0, lots=self.lots)


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def make() -> tuple[FakeSource, Clock, asyncio.Queue[Message], Poller]:
    source, clock, hub = FakeSource(), Clock(), Hub(5)
    queue = hub.subscribe()
    assert queue is not None
    return source, clock, queue, Poller(source, hub, 1.0, 15.0, clock)


async def test_publishes_first_then_only_on_change_or_heartbeat() -> None:
    source, clock, queue, poller = make()
    assert await poller.tick() is True  # first tick
    assert await poller.tick() is False  # nothing changed
    assert queue.qsize() == 1
    source.lots = [lot("lot-a", 1, occupied=3), lot("lot-b", 0)]
    assert await poller.tick() is True
    source.lots = [lot("lot-a", 1, occupied=3)]  # a lot disappeared
    assert await poller.tick() is True
    source.lots = [lot("lot-a", 1, occupied=3), lot("lot-c", 0, capacity=None)]  # and one appeared
    assert await poller.tick() is True
    assert await poller.tick() is False
    clock.now = 14.9
    assert await poller.tick() is False
    clock.now = 15.1
    assert await poller.tick() is True  # heartbeat with no change
    assert queue.qsize() == 4  # the queue holds 4: the oldest was dropped


async def test_gauges_follow_snapshots_and_forget_removed_lots() -> None:
    source, _, _, poller = make()
    source.lots = [lot("gauge-a", 1, occupied=4, capacity=9), lot("gauge-b", 1, capacity=None)]
    await poller.tick()
    assert metrics.registry.get_sample_value("parking_lot_occupied", {"lot": "gauge-a"}) == 4
    assert metrics.registry.get_sample_value("parking_lot_capacity", {"lot": "gauge-a"}) == 9
    source.lots = []
    await poller.tick()
    assert metrics.registry.get_sample_value("parking_lot_occupied", {"lot": "gauge-a"}) is None
    assert metrics.registry.get_sample_value("parking_lot_capacity", {"lot": "gauge-b"}) is None


async def test_database_error_is_logged_and_the_loop_continues(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    source, _, queue, poller = make()
    source.fail = True
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == 4:
            source.fail = False
        if len(sleeps) == 6:
            raise asyncio.CancelledError

    monkeypatch.setattr("campus_parking.realtime.poller.asyncio.sleep", fake_sleep)
    with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
        await poller.run()
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 1.0, 1.0]  # backoff while failing, normal pace after
    assert [r.getMessage() for r in caplog.records] == ["live poll failed"] * 3
    assert "database down" not in caplog.text
    assert queue.qsize() == 1  # recovered and published
