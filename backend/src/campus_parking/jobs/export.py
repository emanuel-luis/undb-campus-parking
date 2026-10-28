"""Parquet archive of a service day: gate events by service date, rejects by received date.

Layout: `<lake>/raw/<dataset>/<partition>=YYYY-MM-DD/part-00000.parquet` + `_manifest.json`. A
partition is written to a hidden sibling directory, checked against the contract, and swapped in
with two renames, so a reader sees the old partition, no partition, or the new one, never a
half-written one. A rerun whose rows hash the same leaves the partition alone ("unchanged").
"""

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from campus_parking.core.clock import day_cutoff
from campus_parking.ingestion.models import GateEventRow, IngestReject
from campus_parking.jobs import archive_contract as contract

PART = "part-00000.parquet"
MANIFEST = "_manifest.json"


@dataclass(frozen=True)
class DatasetExport:
    dataset: str
    status: str  # "written" or "unchanged"
    rows: int
    sha256: str


def partition_dir(lake: Path, dataset: str, partition: str, day: date) -> Path:
    return lake / "raw" / dataset / f"{partition}={day.isoformat()}"


def publish_partition(
    final: Path, dataset: str, run_id: UUID, table: pa.Table, source_count: int
) -> DatasetExport:
    """Write `table` as the partition at `final` unless it already holds the same rows."""
    rows_sha = contract.rows_digest(table)
    manifest_path = final / MANIFEST
    if manifest_path.is_file() and (final / PART).is_file():
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        if current.get("rows_sha256") == rows_sha and current.get("row_count") == table.num_rows:
            return DatasetExport(dataset, "unchanged", table.num_rows, current["sha256"])

    tmp = final.with_name(f".{final.name}.tmp")
    old = final.with_name(f".{final.name}.old")
    for leftover in (tmp, old):
        shutil.rmtree(leftover, ignore_errors=True)
    tmp.mkdir(parents=True)
    pq.write_table(table, tmp / PART)
    file_sha = hashlib.sha256((tmp / PART).read_bytes()).hexdigest()
    body = contract.manifest(
        dataset=dataset,
        run_id=run_id,
        table=table,
        source_row_count=source_count,
        file_sha256=file_sha,
        rows_sha256=rows_sha,
    )
    (tmp / MANIFEST).write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    if final.exists():
        final.rename(old)
    tmp.rename(final)
    shutil.rmtree(old, ignore_errors=True)
    return DatasetExport(dataset, "written", table.num_rows, file_sha)


def _export_sync(
    lake: Path,
    day: date,
    run_id: UUID,
    events: list[dict[str, Any]],
    event_source: int,
    rejects: list[dict[str, Any]],
    reject_source: int,
) -> list[DatasetExport]:
    event_table = pa.Table.from_pylist(events, schema=contract.GATE_EVENTS_SCHEMA)
    reject_table = pa.Table.from_pylist(rejects, schema=contract.REJECTS_SCHEMA)
    # Both checks run before either partition is published.
    contract.check_gate_events(event_table, day, event_source)
    contract.check_rejects(reject_table, reject_source)
    return [
        publish_partition(
            partition_dir(lake, "gate_events", "service_date", day),
            "gate_events",
            run_id,
            event_table,
            event_source,
        ),
        publish_partition(
            partition_dir(lake, "ingest_rejects", "received_date", day),
            "ingest_rejects",
            run_id,
            reject_table,
            reject_source,
        ),
    ]


async def _read_events(session: AsyncSession, day: date) -> tuple[list[dict[str, Any]], int]:
    # One statement, so the rows and the source count come from the same snapshot.
    stmt = (
        select(GateEventRow, func.count().over())
        .where(GateEventRow.service_date == day)
        .order_by(GateEventRow.event_id)
    )
    out: list[dict[str, Any]] = []
    total = 0
    for row, count in await session.execute(stmt):
        total = count
        out.append(
            {
                "schema_version": row.schema_version,
                "event_id": str(row.event_id),
                "gate_id": row.gate_id,
                "lot_id": row.lot_id,
                "direction": row.direction,
                "student_ref": row.student_ref,
                "occurred_at": row.occurred_at,
                "service_date": row.service_date,
                "received_at": row.received_at,
                "batch_id": str(row.batch_id),
                "payload_sha256": row.payload_sha256,
            }
        )
    return out, total


async def _read_rejects(
    session: AsyncSession, day: date, tz: ZoneInfo
) -> tuple[list[dict[str, Any]], int]:
    start, end = day_cutoff(day - timedelta(days=1), tz), day_cutoff(day, tz)
    stmt = (
        select(IngestReject, func.count().over())
        .where(IngestReject.received_at >= start, IngestReject.received_at < end)
        .order_by(IngestReject.reject_id)
    )
    out: list[dict[str, Any]] = []
    total = 0
    for row, count in await session.execute(stmt):
        total = count
        out.append(
            {
                "reject_id": row.reject_id,
                "batch_id": str(row.batch_id),
                "received_at": row.received_at,
                "event_id": None if row.event_id is None else str(row.event_id),
                "service_date": row.service_date,
                "lot_id": row.lot_id,
                "gate_id": row.gate_id,
                "reason": row.reason,
                "detail": row.detail,
                "payload_json": json.dumps(row.payload, sort_keys=True),
            }
        )
    return out, total


async def export_day(
    sessionmaker: async_sessionmaker[AsyncSession],
    lake_dir: Path,
    tz: ZoneInfo,
    day: date,
    run_id: UUID,
) -> list[DatasetExport]:
    """Export both datasets of `day`; the Arrow and file work runs in a worker thread."""
    async with sessionmaker() as session:
        events, event_source = await _read_events(session, day)
        rejects, reject_source = await _read_rejects(session, day, tz)
    return await asyncio.to_thread(
        _export_sync, lake_dir, day, run_id, events, event_source, rejects, reject_source
    )
