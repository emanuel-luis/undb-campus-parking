"""Response models of the ingestion and operator routes."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from campus_parking.ingestion.validation import RejectReason


class RejectItem(BaseModel):
    index: int = Field(description="Position of the item in the request array.")
    event_id: UUID | None = None
    reason: RejectReason
    detail: str


class IngestResult(BaseModel):
    batch_id: UUID
    received: int
    accepted: int
    duplicates: int
    rejected: int
    rejects: list[RejectItem]


class RejectOut(BaseModel):
    reject_id: int
    batch_id: UUID
    received_at: datetime
    event_id: UUID | None
    service_date: date | None
    lot_id: str | None
    gate_id: str | None
    reason: RejectReason
    detail: str
    payload: Any = Field(description="The item as received, capped at 4 KB.")


class RejectPage(BaseModel):
    items: list[RejectOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` for the next page; null at the end."
    )


class MinuteCount(BaseModel):
    minute: datetime
    accepted: int
    duplicates: int
    rejected: int


class Lag(BaseModel):
    avg_s: float | None
    max_s: float | None


class GateHealth(BaseModel):
    gate_id: str
    lot_id: str
    last_event_at: datetime | None
    last_received_at: datetime | None


class IngestionHealth(BaseModel):
    per_minute: list[MinuteCount] = Field(
        description="Minutes with at least one batch, oldest first."
    )
    rejects_by_reason: dict[str, int]
    lag: Lag = Field(description="Arrival lag of accepted events in the window.")
    gates: list[GateHealth]
    last_batch_at: datetime | None
