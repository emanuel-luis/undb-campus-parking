"""Response models of the availability routes (and, later, the live socket)."""

from datetime import datetime

from pydantic import BaseModel, Field

from campus_parking.registry.schemas import Slug


class LotAvailability(BaseModel):
    lot_id: Slug
    name: str
    capacity: int | None = Field(
        description="Current capacity; null before the first period starts."
    )
    occupied: int = Field(description="Open stays, exact (may exceed capacity).")
    free: int | None = Field(description="max(capacity - occupied, 0); null without a capacity.")
    occupancy_pct: float | None = Field(description="occupied / capacity, in percent, 1 decimal.")
    over_capacity: bool
    version: int = Field(description="Increases whenever an accepted event changes the lot.")
    updated_at: datetime
    last_event_at: datetime | None


class AvailabilitySnapshot(BaseModel):
    generated_at: datetime
    lots: list[LotAvailability] = Field(description="Sorted by lot_id.")
