"""Occupancy tables: stays (derived from events) and the per-lot open-stay counter."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from campus_parking.core.db import Base, UTCDateTime

_OPEN = text("status = 'open'")


class Stay(Base):
    __tablename__ = "stays"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'closed', 'missing_exit', 'exit_only', 'closed_by_reconciliation')",
            name="status_valid",
        ),
        Index("ix_stays_lot_id_student_ref_service_date", "lot_id", "student_ref", "service_date"),
        Index("ix_stays_updated_at", "updated_at"),
        Index("ix_stays_lot_id_open", "lot_id", postgresql_where=_OPEN, sqlite_where=_OPEN),
    )

    stay_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    lot_id: Mapped[str] = mapped_column(Text)
    student_ref: Mapped[str] = mapped_column(Text)
    service_date: Mapped[date] = mapped_column(Date)
    entry_event_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    exit_event_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    entered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    exited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(Text)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class LotOccupancy(Base):
    __tablename__ = "lot_occupancy"
    __table_args__ = (CheckConstraint("occupied >= 0", name="occupied_non_negative"),)

    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.lot_id"), primary_key=True)
    occupied: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
