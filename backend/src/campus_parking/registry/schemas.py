"""Request and response models of the registry routes."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from campus_parking.registry.models import SLUG_PATTERN, STUDENT_REF_PATTERN

Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
StudentRef = Annotated[str, StringConstraints(pattern=STUDENT_REF_PATTERN)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Direction = Literal["entry", "exit"]


class CapacityPeriod(BaseModel):
    """Capacity valid from this local date until the next period starts."""

    valid_from: date
    capacity: int = Field(gt=0)


class GateOut(BaseModel):
    gate_id: Slug
    lot_id: Slug
    direction: Direction
    name: str


class LotOut(BaseModel):
    lot_id: Slug
    name: str
    capacity: int | None = Field(
        description=(
            "Capacity of the period with the latest valid_from on or before today (service time "
            "zone). Null when every period starts in the future: there is no current capacity yet."
        )
    )
    capacity_history: list[CapacityPeriod] = Field(description="Sorted by valid_from.")
    gates: list[GateOut] = Field(description="Sorted by gate_id.")


class LotUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    capacity_history: list[CapacityPeriod] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _unique_valid_from(self) -> Self:
        dates = [p.valid_from for p in self.capacity_history]
        if len(set(dates)) != len(dates):
            raise ValueError("capacity_history has repeated valid_from dates")
        return self


class GateUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_id: Slug
    direction: Direction
    name: Name


class StudentBulkUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_refs: list[StudentRef] = Field(
        min_length=1, max_length=1000, description="Repeated refs in one body count once."
    )


class BulkUpsertResult(BaseModel):
    created: int
    unchanged: int
