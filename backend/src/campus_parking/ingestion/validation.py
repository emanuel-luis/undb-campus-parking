"""Validation of one raw batch item against the registry and the clock (pure, no I/O)."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError

from campus_parking.ingestion.contract import GateEvent
from campus_parking.ingestion.sanitize import clean

RejectReason = Literal[
    "schema_invalid",
    "unknown_gate",
    "unknown_student",
    "lot_mismatch",
    "direction_mismatch",
    "in_future",
    "too_old",
    "event_id_conflict",
]
MAX_DETAIL = 200
MAX_ID = 80


@dataclass(frozen=True)
class KnownGate:
    lot_id: str
    direction: str


@dataclass(frozen=True)
class Reject:
    reason: RejectReason
    detail: str
    # Whatever could be read from the item, for the operator's view.
    event_id: UUID | None = None
    gate_id: str | None = None
    lot_id: str | None = None
    occurred_at: datetime | None = None


def _text(raw: Any, key: str) -> str | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    return clean(value[:MAX_ID]) if isinstance(value, str) else None


def _event_id(raw: Any) -> UUID | None:
    try:
        return UUID(str(raw["event_id"])) if isinstance(raw, dict) and "event_id" in raw else None
    except ValueError:
        return None


def _schema_detail(exc: ValidationError) -> str:
    first = exc.errors(include_input=False, include_url=False)[0]
    where = ".".join(str(p) for p in first["loc"]) or "item"
    return str(clean(f"{where}: {first['msg']}"))[:MAX_DETAIL]


def validate_item(
    raw: Any,
    gates: Mapping[str, KnownGate],
    students: Collection[str],
    received_at: datetime,
    max_clock_skew_s: int,
    max_event_age_days: int,
) -> GateEvent | Reject:
    """Check, in order: schema, unknown_gate, unknown_student, lot_mismatch, direction_mismatch,
    in_future, too_old. Position in the batch plays no part."""
    try:
        event = GateEvent.model_validate(raw)
    except ValidationError as exc:
        return Reject(
            "schema_invalid",
            _schema_detail(exc),
            _event_id(raw),
            _text(raw, "gate_id"),
            _text(raw, "lot_id"),
        )

    def reject(reason: RejectReason, detail: str) -> Reject:
        return Reject(
            reason, detail, event.event_id, event.gate_id, event.lot_id, event.occurred_at
        )

    gate = gates.get(event.gate_id)
    if gate is None:
        return reject("unknown_gate", "gate is not registered")
    if event.student_ref not in students:
        return reject("unknown_student", "student is not registered")
    if event.lot_id != gate.lot_id:
        return reject("lot_mismatch", f"gate belongs to lot {gate.lot_id}")
    if event.direction != gate.direction:
        return reject("direction_mismatch", f"gate direction is {gate.direction}")
    if event.occurred_at > received_at + timedelta(seconds=max_clock_skew_s):
        return reject("in_future", f"occurred more than {max_clock_skew_s} s after arrival")
    if event.occurred_at < received_at - timedelta(days=max_event_age_days):
        return reject("too_old", f"occurred more than {max_event_age_days} days before arrival")
    return event
