"""The ingest transaction: one batch in; events, stays, occupancy and health rows out."""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core import metrics
from campus_parking.core.clock import service_date
from campus_parking.core.settings import Settings
from campus_parking.ingestion.contract import GateEvent, payload_hash
from campus_parking.ingestion.models import IngestBatch
from campus_parking.ingestion.repository import IngestionRepository
from campus_parking.ingestion.sanitize import clean
from campus_parking.ingestion.schemas import IngestResult, RejectItem
from campus_parking.ingestion.validation import KnownGate, Reject, validate_item
from campus_parking.occupancy.repository import OccupancyRepository
from campus_parking.occupancy.service import apply_touched
from campus_parking.registry.models import SLUG_PATTERN, STUDENT_REF_PATTERN

logger = logging.getLogger(__name__)

PAYLOAD_CAP_BYTES = 4096
LOCK_NOT_AVAILABLE = "55P03"


def is_lock_timeout(exc: DBAPIError) -> bool:
    """True for Postgres SQLSTATE 55P03 (lock_not_available), however the driver wraps it."""
    error: BaseException | None = exc.orig
    while error is not None:
        if getattr(error, "sqlstate", None) == LOCK_NOT_AVAILABLE:
            return True
        error = error.__cause__
    return False


def capped_payload(raw: Any) -> Any:
    """The item as received, or a marker when it would not fit the 4 KB cap (or is not JSON)."""
    raw = clean(raw)
    try:
        text = json.dumps(raw, allow_nan=False)
    except ValueError:
        return {"truncated": True, "preview": "not representable as JSON"}
    if len(text.encode()) <= PAYLOAD_CAP_BYTES:
        return raw
    return {"truncated": True, "preview": text[:1000]}


@dataclass(frozen=True)
class _Candidate:
    index: int
    event: GateEvent
    digest: str


@dataclass(frozen=True)
class _Rejected:
    index: int
    reject: Reject
    raw: Any


def _ids(raw_items: list[Any], key: str, pattern: str) -> set[str]:
    """Well-formed ids only: anything else cannot match a row and fails validation as
    schema_invalid, so it never reaches the lookups (NUL or lone surrogates would break them)."""
    return {
        item[key]
        for item in raw_items
        if isinstance(item, dict)
        and isinstance(item.get(key), str)
        and re.fullmatch(pattern, item[key])
    }


def _latest(pairs: list[tuple[str, datetime]]) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for key, at in pairs:
        out[key] = max(at, out.get(key, at))
    return out


class IngestionService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repo = IngestionRepository(session)

    async def ingest(self, raw_items: list[Any], received_at: datetime) -> IngestResult:
        started = time.perf_counter()
        cfg = self.settings
        repo = self.repo
        batch_id = uuid4()

        await repo.set_lock_timeout(cfg.ingest_lock_timeout_ms)
        gates = await repo.gates(_ids(raw_items, "gate_id", SLUG_PATTERN))
        known_gates = {g.gate_id: KnownGate(g.lot_id, g.direction) for g in gates.values()}
        students = await repo.known_students(_ids(raw_items, "student_ref", STUDENT_REF_PATTERN))

        candidates: list[_Candidate] = []
        rejected: list[_Rejected] = []
        for index, raw in enumerate(raw_items):
            outcome = validate_item(
                raw,
                known_gates,
                students,
                received_at,
                cfg.max_clock_skew_s,
                cfg.max_event_age_days,
            )
            if isinstance(outcome, Reject):
                rejected.append(_Rejected(index, outcome, raw))
            else:
                candidates.append(_Candidate(index, outcome, payload_hash(outcome)))

        # The batch row goes in first: events and rejects reference it.
        batch = IngestBatch(
            batch_id=batch_id,
            received_at=received_at,
            items=len(raw_items),
            accepted=0,
            duplicates=0,
            rejected=0,
            avg_lag_s=None,
            max_lag_s=None,
            duration_ms=0,
        )
        await repo.add_batch(batch)
        occupancy = OccupancyRepository(self.session)
        if candidates:
            await occupancy.lock_lots(sorted({c.event.lot_id for c in candidates}), received_at)

        # The first item with an id is the one to insert; a later one with the same id is a
        # duplicate or a conflict, judged against what the database ends up holding.
        first: dict[UUID, _Candidate] = {}
        for c in candidates:
            first.setdefault(c.event.event_id, c)
        inserted = await repo.insert_events(
            [
                {
                    "event_id": c.event.event_id,
                    "schema_version": c.event.schema_version,
                    "gate_id": c.event.gate_id,
                    "lot_id": c.event.lot_id,
                    "direction": c.event.direction,
                    "student_ref": c.event.student_ref,
                    "occurred_at": c.event.occurred_at,
                    "service_date": service_date(c.event.occurred_at, cfg.tzinfo),
                    "received_at": received_at,
                    "batch_id": batch_id,
                    "payload_sha256": c.digest,
                }
                for c in first.values()
            ]
        )
        stored = await repo.stored_hashes([i for i in first if i not in inserted])

        accepted: list[_Candidate] = []
        duplicates: list[_Candidate] = []
        for c in candidates:
            event_id = c.event.event_id
            if c is first[event_id] and event_id in inserted:
                accepted.append(c)
                continue
            held = first[event_id].digest if event_id in inserted else stored[event_id]
            if held == c.digest:
                duplicates.append(c)
            else:
                reject = Reject(
                    "event_id_conflict",
                    "event_id already stored with a different payload",
                    event_id,
                    c.event.gate_id,
                    c.event.lot_id,
                    c.event.occurred_at,
                )
                rejected.append(_Rejected(c.index, reject, raw_items[c.index]))

        if accepted:
            keys = {
                (c.event.lot_id, c.event.student_ref, service_date(c.event.occurred_at, cfg.tzinfo))
                for c in accepted
            }
            deltas = await apply_touched(self.session, keys, received_at, cfg.tzinfo)
            newest = _latest([(c.event.lot_id, c.event.occurred_at) for c in accepted])
            await occupancy.apply_changes(deltas, newest, received_at)

        seen = _latest([(c.event.gate_id, c.event.occurred_at) for c in [*accepted, *duplicates]])
        for gate_id in sorted(seen):
            await repo.touch_gate(gate_id, seen[gate_id], received_at)

        rejected.sort(key=lambda r: r.index)
        await repo.add_rejects(
            [
                {
                    "batch_id": batch_id,
                    "received_at": received_at,
                    "event_id": r.reject.event_id,
                    "service_date": (
                        service_date(r.reject.occurred_at, cfg.tzinfo)
                        if r.reject.occurred_at
                        else None
                    ),
                    "lot_id": r.reject.lot_id,
                    "gate_id": r.reject.gate_id,
                    "reason": r.reject.reason,
                    "detail": r.reject.detail,
                    "payload": capped_payload(r.raw),
                }
                for r in rejected
            ]
        )

        lags = [(received_at - c.event.occurred_at).total_seconds() for c in accepted]
        duration_ms = int((time.perf_counter() - started) * 1000)
        batch.accepted = len(accepted)
        batch.duplicates = len(duplicates)
        batch.rejected = len(rejected)
        batch.avg_lag_s = sum(lags) / len(lags) if lags else None
        batch.max_lag_s = max(lags) if lags else None
        batch.duration_ms = duration_ms
        await self.session.commit()

        for c in accepted:
            metrics.events_ingested_total.labels(c.event.lot_id).inc()
        for c in duplicates:
            metrics.events_duplicate_total.labels(c.event.lot_id).inc()
        for r in rejected:
            metrics.events_rejected_total.labels(r.reject.reason).inc()
        for lag in lags:
            metrics.event_lag_seconds.observe(max(lag, 0.0))
        logger.info(
            "ingest batch",
            extra={
                "batch_id": str(batch_id),
                "items": len(raw_items),
                "accepted": len(accepted),
                "duplicates": len(duplicates),
                "rejected": len(rejected),
                "duration_ms": duration_ms,
            },
        )
        return IngestResult(
            batch_id=batch_id,
            received=len(raw_items),
            accepted=len(accepted),
            duplicates=len(duplicates),
            rejected=len(rejected),
            rejects=[
                RejectItem(
                    index=r.index,
                    event_id=r.reject.event_id,
                    reason=r.reject.reason,
                    detail=r.reject.detail,
                )
                for r in rejected
            ],
        )
