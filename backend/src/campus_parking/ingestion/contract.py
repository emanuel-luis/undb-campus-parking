"""The GateEvent v1 contract: what a gate (or the generator) sends to POST /api/v1/events."""

import hashlib
import json
from datetime import UTC
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from campus_parking.registry.schemas import Direction, Slug, StudentRef


class GateEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    event_id: UUID
    gate_id: Slug
    lot_id: Slug
    direction: Direction
    student_ref: StudentRef
    occurred_at: AwareDatetime


def canonical_json(event: GateEvent) -> str:
    """Sorted keys, no spaces, `occurred_at` in UTC with microseconds and a `Z`, so the same
    instant written with another offset or precision hashes the same."""
    data = {
        "schema_version": event.schema_version,
        "event_id": str(event.event_id),
        "gate_id": event.gate_id,
        "lot_id": event.lot_id,
        "direction": event.direction,
        "student_ref": event.student_ref,
        "occurred_at": event.occurred_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def payload_hash(event: GateEvent) -> str:
    return hashlib.sha256(canonical_json(event).encode()).hexdigest()
