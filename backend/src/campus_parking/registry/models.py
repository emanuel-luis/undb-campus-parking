"""Registry tables: lots, their capacity history, gates and the pseudonymous students."""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from campus_parking.core.db import Base, UTCDateTime

STUDENT_REF_PATTERN = r"^stu_[0-9a-f]{12}$"
SLUG_PATTERN = r"^[a-z0-9-]{2,40}$"


class Lot(Base):
    __tablename__ = "lots"

    lot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class LotCapacityHistory(Base):
    __tablename__ = "lot_capacity_history"
    __table_args__ = (CheckConstraint("capacity > 0", name="capacity_positive"),)

    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.lot_id"), primary_key=True)
    valid_from: Mapped[date] = mapped_column(Date, primary_key=True)
    capacity: Mapped[int] = mapped_column(Integer)


class Gate(Base):
    __tablename__ = "gates"
    __table_args__ = (CheckConstraint("direction IN ('entry', 'exit')", name="direction_valid"),)

    gate_id: Mapped[str] = mapped_column(Text, primary_key=True)
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.lot_id"))
    direction: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Student(Base):
    __tablename__ = "students"
    # The regex operator exists on Postgres only; SQLite relies on the Pydantic validation.
    __table_args__ = (
        CheckConstraint(f"student_ref ~ '{STUDENT_REF_PATTERN}'", name="student_ref_format").ddl_if(
            dialect="postgresql"
        ),
    )

    student_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
