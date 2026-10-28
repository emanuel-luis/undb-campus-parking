"""Registry use cases: upserts by natural key and the public lot listing."""

from datetime import date, datetime

from campus_parking.core.problems import GateImmutable, LotNotFound
from campus_parking.registry.domain import current_capacity
from campus_parking.registry.models import Gate, Lot
from campus_parking.registry.repository import RegistryRepository
from campus_parking.registry.schemas import (
    BulkUpsertResult,
    CapacityPeriod,
    GateOut,
    GateUpsert,
    LotOut,
    LotUpsert,
)


def _gate_out(gate: Gate) -> GateOut:
    return GateOut.model_validate(gate, from_attributes=True)


def _lot_out(lot: Lot, history: list[CapacityPeriod], gates: list[Gate], today: date) -> LotOut:
    return LotOut(
        lot_id=lot.lot_id,
        name=lot.name,
        capacity=current_capacity(history, today),
        capacity_history=history,
        gates=[_gate_out(g) for g in gates],
    )


class RegistryService:
    def __init__(self, repo: RegistryRepository) -> None:
        self.repo = repo

    async def list_lots(self, today: date) -> list[LotOut]:
        history = await self.repo.history_by_lot()
        gates = await self.repo.gates_by_lot()
        return [
            _lot_out(lot, history[lot.lot_id], gates[lot.lot_id], today)
            for lot in await self.repo.list_lots()
        ]

    async def upsert_lot(
        self, lot_id: str, body: LotUpsert, now: datetime, today: date
    ) -> tuple[LotOut, bool]:
        """Replace the lot's name and whole capacity history. Returns (lot, created)."""
        history = sorted(body.capacity_history, key=lambda p: p.valid_from)
        created = await self.repo.save_lot(lot_id, body.name, history, now)
        await self.repo.commit()
        lot = await self.repo.get_lot(lot_id)
        assert lot is not None
        gates = (await self.repo.gates_by_lot(lot_id))[lot_id]
        return _lot_out(lot, history, gates, today), created

    async def upsert_gate(
        self, gate_id: str, body: GateUpsert, now: datetime
    ) -> tuple[GateOut, bool]:
        """Create the gate or rename it. Its lot and direction are fixed from creation (the
        append-only events reference them), so a different value is a 409 `gate-immutable`."""
        gate = await self.repo.get_gate(gate_id)
        if gate is None:
            if await self.repo.get_lot(body.lot_id) is None:
                raise LotNotFound(f"Lot {body.lot_id!r} does not exist.")
            gate = Gate(
                gate_id=gate_id,
                lot_id=body.lot_id,
                direction=body.direction,
                name=body.name,
                created_at=now,
                updated_at=now,
            )
            await self.repo.add_gate(gate)
            created = True
        else:
            if (gate.lot_id, gate.direction) != (body.lot_id, body.direction):
                raise GateImmutable(f"Gate {gate_id!r} keeps its lot and direction.")
            if gate.name != body.name:
                gate.name = body.name
                gate.updated_at = now
            created = False
        await self.repo.commit()
        return _gate_out(gate), created

    async def bulk_students(self, refs: list[str], now: datetime) -> BulkUpsertResult:
        unique = list(dict.fromkeys(refs))
        created = await self.repo.insert_students(unique, now)
        await self.repo.commit()
        return BulkUpsertResult(created=created, unchanged=len(unique) - created)
