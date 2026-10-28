"""Registry persistence (SQLAlchemy 2 async). The caller owns the transaction."""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.db import insert_ignore
from campus_parking.registry.models import Gate, Lot, LotCapacityHistory, Student
from campus_parking.registry.schemas import CapacityPeriod


class RegistryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()

    async def get_lot(self, lot_id: str) -> Lot | None:
        return await self.session.get(Lot, lot_id)

    async def list_lots(self) -> list[Lot]:
        return list((await self.session.scalars(select(Lot).order_by(Lot.lot_id))).all())

    async def history_by_lot(self, lot_id: str | None = None) -> dict[str, list[CapacityPeriod]]:
        stmt = select(LotCapacityHistory).order_by(
            LotCapacityHistory.lot_id, LotCapacityHistory.valid_from
        )
        if lot_id is not None:
            stmt = stmt.where(LotCapacityHistory.lot_id == lot_id)
        out: dict[str, list[CapacityPeriod]] = defaultdict(list)
        for row in await self.session.scalars(stmt):
            out[row.lot_id].append(CapacityPeriod(valid_from=row.valid_from, capacity=row.capacity))
        return out

    async def gates_by_lot(self, lot_id: str | None = None) -> dict[str, list[Gate]]:
        stmt = select(Gate).order_by(Gate.gate_id)
        if lot_id is not None:
            stmt = stmt.where(Gate.lot_id == lot_id)
        out: dict[str, list[Gate]] = defaultdict(list)
        for gate in await self.session.scalars(stmt):
            out[gate.lot_id].append(gate)
        return out

    def _dialect(self) -> str:
        return self.session.get_bind().dialect.name

    async def save_lot(
        self, lot_id: str, name: str, history: list[CapacityPeriod], now: datetime
    ) -> bool:
        """Create the lot or rename it, and replace its capacity history. True when created.

        Insert-or-ignore first, so two concurrent creates of the same lot never hit the primary
        key: the loser gets no row back and takes the update path, whose UPDATE also holds the
        row lock that serializes the history replacement below."""
        inserted = await self.session.execute(
            insert_ignore(self._dialect(), Lot)
            .values(lot_id=lot_id, name=name, created_at=now, updated_at=now)
            .returning(Lot.lot_id)
        )
        created = inserted.first() is not None
        if not created:
            await self.session.execute(
                update(Lot).where(Lot.lot_id == lot_id).values(name=name, updated_at=now)
            )
        await self.session.execute(
            delete(LotCapacityHistory).where(LotCapacityHistory.lot_id == lot_id)
        )
        self.session.add_all(
            LotCapacityHistory(lot_id=lot_id, valid_from=p.valid_from, capacity=p.capacity)
            for p in history
        )
        await self.session.flush()
        return created

    async def get_gate(self, gate_id: str) -> Gate | None:
        return await self.session.get(Gate, gate_id)

    async def lock_gate(self, gate_id: str) -> Gate | None:
        """The gate row with a row lock (FOR UPDATE on Postgres; SQLite locks the database)."""
        stmt = (
            select(Gate)
            .where(Gate.gate_id == gate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(stmt)

    async def insert_gate(
        self, gate_id: str, lot_id: str, direction: str, name: str, now: datetime
    ) -> bool:
        """Insert-or-ignore the gate; False when another transaction created it first."""
        inserted = await self.session.execute(
            insert_ignore(self._dialect(), Gate)
            .values(
                gate_id=gate_id,
                lot_id=lot_id,
                direction=direction,
                name=name,
                created_at=now,
                updated_at=now,
            )
            .returning(Gate.gate_id)
        )
        return inserted.first() is not None

    async def insert_students(self, refs: list[str], now: datetime) -> int:
        """Insert the missing refs in one statement; returns how many were really inserted."""
        stmt = (
            insert_ignore(self._dialect(), Student)
            .values([{"student_ref": r, "created_at": now, "updated_at": now} for r in refs])
            .returning(Student.student_ref)
        )
        return len((await self.session.execute(stmt)).all())
