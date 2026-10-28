"""Ingestion tables: batches, the append-only event log and per-item rejects."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from campus_parking.core.db import Base, UTCDateTime


class IngestBatch(Base):
    __tablename__ = "ingest_batches"
    __table_args__ = (Index("ix_ingest_batches_received_at", "received_at"),)

    batch_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    items: Mapped[int] = mapped_column(Integer)
    accepted: Mapped[int] = mapped_column(Integer)
    duplicates: Mapped[int] = mapped_column(Integer)
    rejected: Mapped[int] = mapped_column(Integer)
    avg_lag_s: Mapped[float | None] = mapped_column(Float)
    max_lag_s: Mapped[float | None] = mapped_column(Float)
    duration_ms: Mapped[int] = mapped_column(Integer)


class GateEventRow(Base):
    """Append-only on Postgres: a trigger rejects UPDATE and DELETE (see migration 0002)."""

    __tablename__ = "gate_events"
    __table_args__ = (
        Index("ix_gate_events_lot_id", "lot_id"),
        Index("ix_gate_events_student_ref", "student_ref"),
        Index("ix_gate_events_service_date", "service_date"),
        Index("ix_gate_events_service_date_lot_id", "service_date", "lot_id"),
        Index("ix_gate_events_received_at", "received_at"),
    )

    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    gate_id: Mapped[str] = mapped_column(ForeignKey("gates.gate_id"))
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.lot_id"))
    direction: Mapped[str] = mapped_column(Text)
    student_ref: Mapped[str] = mapped_column(ForeignKey("students.student_ref"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    service_date: Mapped[date] = mapped_column(Date)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("ingest_batches.batch_id"))
    payload_sha256: Mapped[str] = mapped_column(Text)


class IngestReject(Base):
    __tablename__ = "ingest_rejects"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('schema_invalid', 'unknown_gate', 'unknown_student', 'lot_mismatch', "
            "'direction_mismatch', 'in_future', 'too_old', 'event_id_conflict')",
            name="reason_valid",
        ),
        # Read newest first; a b-tree serves that by scanning backwards.
        Index("ix_ingest_rejects_received_at", "received_at"),
    )

    reject_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("ingest_batches.batch_id"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    event_id: Mapped[UUID | None] = mapped_column(Uuid)
    service_date: Mapped[date | None] = mapped_column(Date)
    lot_id: Mapped[str | None] = mapped_column(Text)
    gate_id: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    payload: Mapped[Any] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
