"""Occupancy use cases: re-derive stays for touched student-days, and availability."""

from collections import Counter
from collections.abc import Collection
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.clock import day_cutoff, service_date
from campus_parking.core.problems import LotNotFound
from campus_parking.occupancy.domain import lot_figures
from campus_parking.occupancy.pairing import pair_stays
from campus_parking.occupancy.repository import OccupancyRepository, StayKey
from campus_parking.occupancy.schemas import AvailabilitySnapshot, LotAvailability
from campus_parking.registry.domain import current_capacity
from campus_parking.registry.repository import RegistryRepository


async def apply_touched(
    session: AsyncSession, touched_keys: Collection[StayKey], now: datetime, tz: ZoneInfo
) -> dict[str, int]:
    """Rebuild the stays of the touched (lot, student, service day) keys from all their events.

    Returns, per lot, the change in open stays; the caller adds it to `lot_occupancy`.
    Stays of a day are deleted and re-inserted, so arrival order cannot matter. A day that
    close_day has closed keeps its closed semantics: an entry without exit stays
    `closed_by_reconciliation` at the day's cutoff, never `open`.
    """
    # ponytail: one statement for all keys; chunk it if a caller ever passes thousands of keys.
    keys = sorted(touched_keys)
    repo = OccupancyRepository(session)
    before = await repo.open_by_lot(keys)
    events = await repo.events_by_key(keys)
    closed = await repo.closed_days({day for _, _, day in keys})
    rows = []
    after: Counter[str] = Counter()
    for lot_id, student_ref, day in keys:
        is_closed = day in closed
        cutoff = day_cutoff(day, tz) if is_closed else None
        for stay in pair_stays(events.get((lot_id, student_ref, day), []), is_closed, cutoff):
            after[lot_id] += stay.status == "open"
            rows.append(
                {
                    "stay_id": stay.stay_id,
                    "lot_id": lot_id,
                    "student_ref": student_ref,
                    "service_date": day,
                    "entry_event_id": stay.entry_event_id,
                    "exit_event_id": stay.exit_event_id,
                    "entered_at": stay.entered_at,
                    "exited_at": stay.exited_at,
                    "ended_at": stay.ended_at,
                    "status": stay.status,
                    "duration_s": stay.duration_s,
                    "updated_at": now,
                }
            )
    await repo.replace_stays(keys, rows)
    return {lot_id: after[lot_id] - before[lot_id] for lot_id in {k[0] for k in keys}}


class OccupancyService:
    def __init__(self, session: AsyncSession, tz: ZoneInfo) -> None:
        self.repo = OccupancyRepository(session)
        self.registry = RegistryRepository(session)
        self.tz = tz

    async def _availability(self, now: datetime, lot_id: str | None) -> list[LotAvailability]:
        history = await self.registry.history_by_lot(lot_id)
        today = service_date(now, self.tz)
        out = []
        for lot, occ in await self.repo.availability_rows(lot_id):
            capacity = current_capacity(history[lot.lot_id], today)
            free, pct, over = lot_figures(capacity, occ.occupied)
            out.append(
                LotAvailability(
                    lot_id=lot.lot_id,
                    name=lot.name,
                    capacity=capacity,
                    occupied=occ.occupied,
                    free=free,
                    occupancy_pct=pct,
                    over_capacity=over,
                    version=occ.version,
                    updated_at=occ.updated_at,
                    last_event_at=occ.last_event_at,
                )
            )
        return out

    async def versions(self) -> dict[str, int]:
        return await self.repo.versions()

    async def snapshot(self, now: datetime) -> AvailabilitySnapshot:
        return AvailabilitySnapshot(generated_at=now, lots=await self._availability(now, None))

    async def lot(self, lot_id: str, now: datetime) -> LotAvailability:
        found = await self._availability(now, lot_id)
        if not found:
            raise LotNotFound(f"Lot {lot_id!r} does not exist.")
        return found[0]
