"""Occupancy persistence (SQLAlchemy 2 async). The caller owns the transaction."""

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.db import insert_ignore, never_backwards
from campus_parking.ingestion.models import GateEventRow
from campus_parking.occupancy.models import LotOccupancy, Stay
from campus_parking.occupancy.pairing import StayEvent
from campus_parking.registry.models import Lot

# (lot_id, student_ref, service_date): the unit stays are derived for.
StayKey = tuple[str, str, date]


class OccupancyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_lots(self, lot_ids: Sequence[str], now: datetime) -> None:
        """Row-lock the lots' counters in lot_id order (no deadlocks). A missing counter row is
        created first, so the invariant "one row per lot" also heals here."""
        ids = sorted(set(lot_ids))
        dialect = self.session.get_bind().dialect.name
        await self.session.execute(
            insert_ignore(dialect, LotOccupancy).values(
                [{"lot_id": i, "occupied": 0, "version": 0, "updated_at": now} for i in ids]
            )
        )
        # FOR UPDATE is a no-op on SQLite, whose writer already holds the whole database.
        await self.session.execute(
            select(LotOccupancy.lot_id)
            .where(LotOccupancy.lot_id.in_(ids))
            .order_by(LotOccupancy.lot_id)
            .with_for_update()
        )

    async def open_by_lot(self, keys: Sequence[StayKey]) -> Counter[str]:
        stmt = (
            select(Stay.lot_id, func.count())
            .where(tuple_(Stay.lot_id, Stay.student_ref, Stay.service_date).in_(keys))
            .where(Stay.status == "open")
            .group_by(Stay.lot_id)
        )
        return Counter({lot: n for lot, n in (await self.session.execute(stmt))})

    async def events_by_key(self, keys: Sequence[StayKey]) -> dict[StayKey, list[StayEvent]]:
        stmt = select(
            GateEventRow.lot_id,
            GateEventRow.student_ref,
            GateEventRow.service_date,
            GateEventRow.event_id,
            GateEventRow.direction,
            GateEventRow.occurred_at,
        ).where(
            tuple_(GateEventRow.lot_id, GateEventRow.student_ref, GateEventRow.service_date).in_(
                keys
            )
        )
        out: dict[StayKey, list[StayEvent]] = defaultdict(list)
        for lot, ref, day, event_id, direction, occurred_at in await self.session.execute(stmt):
            out[(lot, ref, day)].append(StayEvent(event_id, direction, occurred_at))
        return out

    async def replace_stays(self, keys: Sequence[StayKey], rows: list[dict[str, Any]]) -> None:
        await self.session.execute(
            delete(Stay).where(tuple_(Stay.lot_id, Stay.student_ref, Stay.service_date).in_(keys))
        )
        if rows:
            await self.session.execute(insert(Stay), rows)

    async def apply_changes(
        self, deltas: dict[str, int], last_event_at: dict[str, datetime], now: datetime
    ) -> None:
        """Add each lot's change in open stays and bump its version (rows locked by lock_lots)."""
        for lot_id in sorted(deltas):
            await self.session.execute(
                update(LotOccupancy)
                .where(LotOccupancy.lot_id == lot_id)
                .values(
                    occupied=LotOccupancy.occupied + deltas[lot_id],
                    version=LotOccupancy.version + 1,
                    updated_at=now,
                    last_event_at=never_backwards(
                        LotOccupancy.last_event_at, last_event_at[lot_id]
                    ),
                )
            )

    async def availability_rows(self, lot_id: str | None = None) -> list[tuple[Lot, LotOccupancy]]:
        stmt = (
            select(Lot, LotOccupancy)
            .join(LotOccupancy, LotOccupancy.lot_id == Lot.lot_id)
            .order_by(Lot.lot_id)
        )
        if lot_id is not None:
            stmt = stmt.where(Lot.lot_id == lot_id)
        return [(lot, occ) for lot, occ in (await self.session.execute(stmt))]
