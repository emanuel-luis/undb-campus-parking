"""Ingestion persistence (SQLAlchemy 2 async). The caller owns the transaction."""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.db import insert_ignore, never_backwards
from campus_parking.ingestion.models import GateEventRow, IngestBatch, IngestReject
from campus_parking.registry.models import Gate, Student


class IngestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def dialect(self) -> str:
        return self.session.get_bind().dialect.name

    async def set_lock_timeout(self, ms: int) -> None:
        """SET LOCAL lock_timeout for this transaction (Postgres only)."""
        if self.dialect == "postgresql":
            await self.session.execute(
                text("SELECT set_config('lock_timeout', :ms, true)"), {"ms": f"{ms}ms"}
            )

    async def gates(self, gate_ids: Collection[str]) -> dict[str, Gate]:
        stmt = select(Gate).where(Gate.gate_id.in_(gate_ids))
        return {g.gate_id: g for g in await self.session.scalars(stmt)}

    async def known_students(self, refs: Collection[str]) -> set[str]:
        stmt = select(Student.student_ref).where(Student.student_ref.in_(refs))
        return set(await self.session.scalars(stmt))

    async def add_batch(self, batch: IngestBatch) -> None:
        """The batch row goes in first: events and rejects reference it."""
        self.session.add(batch)
        await self.session.flush()

    async def insert_events(self, rows: list[dict[str, Any]]) -> set[UUID]:
        """Insert-ignore the events; returns the ids that were really inserted."""
        if not rows:
            return set()
        stmt = (
            insert_ignore(self.dialect, GateEventRow).values(rows).returning(GateEventRow.event_id)
        )
        return set((await self.session.scalars(stmt)).all())

    async def stored_hashes(self, event_ids: Collection[UUID]) -> dict[UUID, str]:
        stmt = select(GateEventRow.event_id, GateEventRow.payload_sha256).where(
            GateEventRow.event_id.in_(event_ids)
        )
        return {i: h for i, h in (await self.session.execute(stmt))}

    async def touch_gate(
        self, gate_id: str, last_event_at: datetime, received_at: datetime
    ) -> None:
        await self.session.execute(
            update(Gate)
            .where(Gate.gate_id == gate_id)
            .values(
                last_event_at=never_backwards(Gate.last_event_at, last_event_at),
                last_received_at=never_backwards(Gate.last_received_at, received_at),
            )
        )

    async def add_rejects(self, rows: Sequence[dict[str, Any]]) -> None:
        if rows:
            await self.session.execute(insert(IngestReject), list(rows))
