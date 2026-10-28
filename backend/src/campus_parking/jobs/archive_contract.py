"""The Parquet archive contract, version 1: what an exported day must look like.

`GateEventArchiveRow` is the row contract and `GATE_EVENTS_SCHEMA` its Arrow form; the check
runs before anything is published, so a bad export never replaces a good partition.
"""

import hashlib
import json
from datetime import date
from typing import Any, Literal
from uuid import UUID

import pyarrow as pa
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from campus_parking.registry.schemas import Direction, Slug, StudentRef

SCHEMA_VERSION = 1

GATE_EVENTS_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.int32(), nullable=False),
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("gate_id", pa.string(), nullable=False),
        pa.field("lot_id", pa.string(), nullable=False),
        pa.field("direction", pa.string(), nullable=False),
        pa.field("student_ref", pa.string(), nullable=False),
        pa.field("occurred_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("service_date", pa.date32(), nullable=False),
        pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("batch_id", pa.string(), nullable=False),
        pa.field("payload_sha256", pa.string(), nullable=False),
    ]
)

REJECTS_SCHEMA = pa.schema(
    [
        pa.field("reject_id", pa.int64(), nullable=False),
        pa.field("batch_id", pa.string(), nullable=False),
        pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("event_id", pa.string()),
        pa.field("service_date", pa.date32()),
        pa.field("lot_id", pa.string()),
        pa.field("gate_id", pa.string()),
        pa.field("reason", pa.string(), nullable=False),
        pa.field("detail", pa.string(), nullable=False),
        pa.field("payload_json", pa.string(), nullable=False),
    ]
)


class GateEventArchiveRow(BaseModel):
    """One row of `raw/gate_events`, schema version 1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    event_id: UUID
    gate_id: Slug
    lot_id: Slug
    direction: Direction
    student_ref: StudentRef
    occurred_at: AwareDatetime
    service_date: date
    received_at: AwareDatetime
    batch_id: UUID
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArchiveContractError(Exception):
    """The table does not meet the contract; `problems` lists the failed checks."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def check_gate_events(table: pa.Table, day: date, source_count: int) -> None:
    """Raise ArchiveContractError unless the table is a valid export of `day`."""
    if not table.schema.equals(GATE_EVENTS_SCHEMA):
        raise ArchiveContractError(["arrow schema differs from the contract"])
    problems: list[str] = []
    if table.num_rows != source_count:
        problems.append(f"row count {table.num_rows} differs from the source count {source_count}")
    rows = table.to_pylist()
    if len({r["event_id"] for r in rows}) != len(rows):
        problems.append("event_id is not unique")
    wrong_day = sum(r["service_date"] != day for r in rows)
    if wrong_day:
        problems.append(f"{wrong_day} rows have a service_date other than {day}")
    invalid = 0
    for row in rows:
        try:
            GateEventArchiveRow.model_validate(row)
        except ValidationError:
            invalid += 1
    if invalid:
        problems.append(f"{invalid} rows do not validate against GateEventArchiveRow v1")
    if problems:
        raise ArchiveContractError(problems)


def check_rejects(table: pa.Table, source_count: int) -> None:
    if not table.schema.equals(REJECTS_SCHEMA):
        raise ArchiveContractError(["arrow schema differs from the contract"])
    if table.num_rows != source_count:
        raise ArchiveContractError(
            [f"row count {table.num_rows} differs from the source count {source_count}"]
        )


def rows_digest(table: pa.Table) -> str:
    """sha256 over the rows in table order: equal data gives an equal digest whatever the bytes
    of the Parquet file (writer version, metadata) are."""
    digest = hashlib.sha256()
    for row in table.to_pylist():
        digest.update(json.dumps(row, sort_keys=True, default=str).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def manifest(
    *,
    dataset: str,
    run_id: UUID,
    table: pa.Table,
    source_row_count: int,
    file_sha256: str,
    rows_sha256: str,
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_id),
        "row_count": table.num_rows,
        "source_row_count": source_row_count,
        "sha256": file_sha256,
        "rows_sha256": rows_sha256,
        "columns": [{"name": f.name, "type": str(f.type)} for f in table.schema],
    }
