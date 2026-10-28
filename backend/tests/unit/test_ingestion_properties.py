"""Properties 1, 8 and 10 against the real IngestionService on in-memory SQLite (few examples)."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus_parking.core.db import Base, create_engine, create_sessionmaker
from campus_parking.ingestion.service import IngestionService
from campus_parking.occupancy.models import LotOccupancy, Stay
from campus_parking.registry.models import Gate, Lot, Student
from tests.conftest import make_settings
from tests.unit.test_properties import BASE, LOTS, STUDENTS, Ev, scenario

PROFILE = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
NOW = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)
assert NOW == BASE


def raw(e: Ev) -> dict[str, Any]:
    suffix = "in" if e.direction == "entry" else "out"
    return {
        "schema_version": 1,
        "event_id": str(e.event_id),
        "gate_id": f"{e.lot}-{suffix}",
        "lot_id": e.lot,
        "direction": e.direction,
        "student_ref": e.student,
        "occurred_at": e.at.isoformat(),
    }


async def with_service(
    body: Callable[[async_sessionmaker[AsyncSession], Any], Awaitable[Any]],
) -> Any:
    cfg = make_settings()
    engine = create_engine(cfg)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = create_sessionmaker(engine)
        async with sessions() as s:
            for lot in LOTS:
                s.add(Lot(lot_id=lot, name=lot, created_at=NOW, updated_at=NOW))
                s.add(LotOccupancy(lot_id=lot, occupied=0, version=0, updated_at=NOW))
            await s.flush()
            for lot in LOTS:
                for suffix, direction in (("in", "entry"), ("out", "exit")):
                    s.add(
                        Gate(
                            gate_id=f"{lot}-{suffix}",
                            lot_id=lot,
                            direction=direction,
                            name="g",
                            created_at=NOW,
                            updated_at=NOW,
                        )
                    )
            s.add_all(Student(student_ref=r, created_at=NOW, updated_at=NOW) for r in STUDENTS)
            await s.commit()
        return await body(sessions, cfg)
    finally:
        await engine.dispose()


async def send(sessions: async_sessionmaker[AsyncSession], cfg: Any, batch: list[Ev]) -> Any:
    async with sessions() as session:
        return await IngestionService(session, cfg).ingest([raw(e) for e in batch], NOW)


async def state(sessions: async_sessionmaker[AsyncSession]) -> tuple[Any, Any]:
    async with sessions() as s:
        stays = sorted(
            (
                (
                    x.lot_id,
                    x.student_ref,
                    x.service_date,
                    x.status,
                    x.entry_event_id,
                    x.exit_event_id,
                    x.entered_at,
                    x.ended_at,
                    x.duration_s,
                )
                for x in await s.scalars(select(Stay))
            ),
            key=repr,
        )
        occupied = {o.lot_id: o.occupied for o in await s.scalars(select(LotOccupancy))}
    return stays, occupied


@PROFILE
@given(scenario())
def test_1_service_state_does_not_depend_on_arrival(data: tuple[list[Ev], list[list[Ev]]]) -> None:
    events, batches = data

    async def shuffled(sessions: async_sessionmaker[AsyncSession], cfg: Any) -> Any:
        for batch in batches:
            await send(sessions, cfg, batch)
        return await state(sessions)

    async def ordered(sessions: async_sessionmaker[AsyncSession], cfg: Any) -> Any:
        await send(sessions, cfg, sorted(events, key=lambda e: (e.at, e.event_id)))
        return await state(sessions)

    got = asyncio.run(with_service(shuffled))
    assert got == asyncio.run(with_service(ordered))
    stays, occupied = got
    open_stays = sum(1 for s in stays if s[3] == "open")
    assert sum(occupied.values()) == open_stays
    assert all(n >= 0 for n in occupied.values())


@PROFILE
@given(scenario())
def test_8_resending_everything_changes_nothing(data: tuple[list[Ev], list[list[Ev]]]) -> None:
    _, batches = data

    async def body(sessions: async_sessionmaker[AsyncSession], cfg: Any) -> Any:
        for batch in batches:
            await send(sessions, cfg, batch)
        before = await state(sessions)
        results = [await send(sessions, cfg, batch) for batch in batches]
        return before, await state(sessions), results

    before, after, results = asyncio.run(with_service(body))
    assert before == after
    assert all(r.accepted == 0 and r.rejected == 0 for r in results)
    assert sum(r.duplicates for r in results) == sum(len(b) for b in batches)


BREAKS: list[dict[str, Any]] = [
    {},
    {"gate_id": "ghost-gate"},
    {"student_ref": "stu_ffffffffffff"},
    {"lot_id": "lot-b"},
    {"direction": "exit"},
    {"occurred_at": "2026-10-28T15:00:00Z"},
    {"occurred_at": "2025-01-01T00:00:00Z"},
    {"schema_version": 9},
]


@st.composite
def broken_batch(draw: st.DrawFn) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Items with unique ids and one flaw each (or none), and the same items shuffled."""
    flaws = draw(st.lists(st.sampled_from(BREAKS), min_size=1, max_size=12))
    items = [
        {
            "schema_version": 1,
            "event_id": str(UUID(int=i + 1)),
            "gate_id": "lot-a-in",
            "lot_id": "lot-a",
            "direction": "entry",
            "student_ref": STUDENTS[i % len(STUDENTS)],
            "occurred_at": "2026-10-28T13:50:00Z",
            **flaw,
        }
        for i, flaw in enumerate(flaws)
    ]
    return items, draw(st.permutations(items))


async def outcomes(
    sessions: async_sessionmaker[AsyncSession], cfg: Any, items: list[dict[str, Any]]
) -> dict[str, str]:
    async with sessions() as session:
        result = await IngestionService(session, cfg).ingest(items, NOW)
    reasons = {str(r.event_id): r.reason for r in result.rejects}
    return {i["event_id"]: reasons.get(i["event_id"], "accepted") for i in items}


@PROFILE
@given(broken_batch())
def test_10_outcome_per_event_does_not_depend_on_batch_order(
    data: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    items, shuffled = data

    async def run_with(order: list[dict[str, Any]]) -> dict[str, str]:
        async def body(sessions: async_sessionmaker[AsyncSession], cfg: Any) -> dict[str, str]:
            return await outcomes(sessions, cfg, order)

        result: dict[str, str] = await with_service(body)
        return result

    assert asyncio.run(run_with(shuffled)) == asyncio.run(run_with(items))
