"""Background task: notice changes in `lot_occupancy` and publish full snapshots to the hub."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus_parking.core import metrics
from campus_parking.core.clock import get_now
from campus_parking.occupancy.schemas import AvailabilitySnapshot
from campus_parking.occupancy.service import OccupancyService
from campus_parking.realtime.hub import Hub

logger = logging.getLogger(__name__)

MAX_BACKOFF_S = 30.0


class SnapshotSource(Protocol):
    async def versions(self) -> dict[str, int]: ...

    async def snapshot(self) -> AvailabilitySnapshot: ...


class DbSource:
    """The occupancy service behind one short session per call."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], tz: ZoneInfo) -> None:
        self.sessionmaker = sessionmaker
        self.tz = tz

    async def versions(self) -> dict[str, int]:
        async with self.sessionmaker() as session:
            return await OccupancyService(session, self.tz).versions()

    async def snapshot(self) -> AvailabilitySnapshot:
        async with self.sessionmaker() as session:
            return await OccupancyService(session, self.tz).snapshot(get_now())


def snapshot_json(snapshot: AvailabilitySnapshot) -> str:
    return snapshot.model_dump_json()


class Poller:
    def __init__(
        self,
        source: SnapshotSource,
        hub: Hub,
        interval_s: float,
        heartbeat_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self.hub = hub
        self.interval_s = interval_s
        self.heartbeat_s = heartbeat_s
        self.clock = clock
        self._seen: dict[str, int] | None = None
        self._last_publish = 0.0
        self._gauge_lots: set[str] = set()

    async def tick(self) -> bool:
        """One poll: publish when versions changed or the heartbeat is due. True if it did."""
        versions = await self.source.versions()
        if versions == self._seen and self.clock() - self._last_publish < self.heartbeat_s:
            return False
        snapshot = await self.source.snapshot()
        self._seen = {lot.lot_id: lot.version for lot in snapshot.lots}
        self._last_publish = self.clock()
        self._set_gauges(snapshot)
        self.hub.publish(snapshot_json(snapshot))
        return True

    def _set_gauges(self, snapshot: AvailabilitySnapshot) -> None:
        for lot in snapshot.lots:
            metrics.lot_occupied.labels(lot.lot_id).set(lot.occupied)
            if lot.capacity is not None:
                metrics.lot_capacity.labels(lot.lot_id).set(lot.capacity)
            else:  # capacity went back to none: no series rather than a stale one
                with contextlib.suppress(KeyError):
                    metrics.lot_capacity.remove(lot.lot_id)
        current = {lot.lot_id for lot in snapshot.lots}
        for gone in self._gauge_lots - current:
            for gauge in (metrics.lot_occupied, metrics.lot_capacity):
                with contextlib.suppress(KeyError):  # no capacity series without a capacity
                    gauge.remove(gone)
        self._gauge_lots = current

    async def run(self) -> None:
        """Poll until cancelled. A failing tick is logged (exception type only: no parameters,
        no payloads) and retried with exponential backoff."""
        failures = 0
        delay = self.interval_s
        while True:
            await asyncio.sleep(delay)  # sleep first: startup is busy enough
            try:
                await self.tick()
                failures = 0
            except Exception as exc:  # noqa: BLE001 - the loop must outlive any database or mapping error
                failures += 1
                logger.error("live poll failed", extra={"error": type(exc).__name__})
            delay = min(self.interval_s * 2**failures, MAX_BACKOFF_S)
