"""Registry persistence (SQLAlchemy 2 async). The caller owns the transaction."""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, select
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

    async def save_lot(
        self, lot_id: str, name: str, history: list[CapacityPeriod], now: datetime
    ) -> bool:
        """Create the lot or rename it, and replace its capacity history. True when created."""
        lot = await self.get_lot(lot_id)
        created = lot is None
        if lot is None:
            self.session.add(Lot(lot_id=lot_id, name=name, created_at=now, updated_at=now))
        else:
            lot.name = name
            lot.updated_at = now
        await self.session.flush()
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

    async def add_gate(self, gate: Gate) -> None:
        self.session.add(gate)
        await self.session.flush()

    async def insert_students(self, refs: list[str], now: datetime) -> int:
        """Insert the missing refs in one statement; returns how many were really inserted."""
        stmt = (
            insert_ignore(self.session.get_bind().dialect.name, Student)
            .values([{"student_ref": r, "created_at": now, "updated_at": now} for r in refs])
            .returning(Student.student_ref)
        )
        return len((await self.session.execute(stmt)).all())
