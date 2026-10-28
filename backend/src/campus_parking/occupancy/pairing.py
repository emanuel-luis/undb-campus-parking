"""Pure pairing of one student-day's gate events into stays (no I/O)."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

StayStatus = Literal["open", "closed", "missing_exit", "exit_only", "closed_by_reconciliation"]


@dataclass(frozen=True)
class StayEvent:
    event_id: UUID
    direction: str  # "entry" or "exit"
    occurred_at: datetime


@dataclass(frozen=True)
class StayRecord:
    stay_id: UUID  # the entry event id, else the exit event id
    status: StayStatus
    entry_event_id: UUID | None
    exit_event_id: UUID | None
    entered_at: datetime | None
    exited_at: datetime | None
    ended_at: datetime | None
    duration_s: int | None  # closed stays only


def _order(event: StayEvent) -> tuple[datetime, UUID]:
    return event.occurred_at, event.event_id


def pair_stays(
    events: Iterable[StayEvent], day_closed: bool, cutoff: datetime | None
) -> list[StayRecord]:
    """Derive the stays of one (lot, student, service day) from all its events.

    Events are sorted by (occurred_at, event_id), so arrival order never matters:
    - an entry with nothing open opens a stay; an entry while one is open turns the previous
      stay into `missing_exit`, ended at the new entry;
    - an exit while a stay is open closes it; an exit with nothing open is `exit_only`;
    - a stay still open at the end stays `open`, or becomes `closed_by_reconciliation` ended at
      `cutoff` when the day is closed.
    """
    if day_closed and cutoff is None:
        raise ValueError("a closed day needs its cutoff")
    stays: list[StayRecord] = []
    open_entry: StayEvent | None = None
    for event in sorted(events, key=_order):
        if event.direction == "entry":
            if open_entry is not None:
                stays.append(_missing_exit(open_entry, event.occurred_at))
            open_entry = event
        elif open_entry is not None:
            seconds = int((event.occurred_at - open_entry.occurred_at).total_seconds())
            stays.append(
                StayRecord(
                    open_entry.event_id,
                    "closed",
                    open_entry.event_id,
                    event.event_id,
                    open_entry.occurred_at,
                    event.occurred_at,
                    event.occurred_at,
                    seconds,
                )
            )
            open_entry = None
        else:
            stays.append(
                StayRecord(
                    event.event_id,
                    "exit_only",
                    None,
                    event.event_id,
                    None,
                    event.occurred_at,
                    event.occurred_at,
                    None,
                )
            )
    if open_entry is not None:
        status: StayStatus = "closed_by_reconciliation" if day_closed else "open"
        ended = cutoff if day_closed else None
        stays.append(
            StayRecord(
                open_entry.event_id,
                status,
                open_entry.event_id,
                None,
                open_entry.occurred_at,
                None,
                ended,
                None,
            )
        )
    return stays


def _missing_exit(entry: StayEvent, ended_at: datetime) -> StayRecord:
    return StayRecord(
        entry.event_id,
        "missing_exit",
        entry.event_id,
        None,
        entry.occurred_at,
        None,
        ended_at,
        None,
    )
