"""Report logic: date ranges, the per-warehouse-run cache and ETags."""

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.problems import InvalidDateRange, LotNotFound
from campus_parking.reports import repository
from campus_parking.reports.schemas import ReportFreshness

DEFAULT_DAYS = 30
MAX_DAYS = 366
MAX_HOURLY_DAYS = 31
CACHE_ENTRIES = 512


def resolve_range(
    start: date | None, end: date | None, today: date, max_days: int = MAX_DAYS
) -> tuple[date, date]:
    """Defaults: `end` today, `start` 29 days before `end` (30 days). Inclusive on both ends."""
    end = end or today
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise InvalidDateRange(f"from ({start}) is after to ({end}).")
    if (end - start).days + 1 > max_days:
        raise InvalidDateRange(f"The range covers more than {max_days} days.")
    return start, end


def etag(run_id: str, path: str, params: dict[str, Any]) -> str:
    query = json.dumps(params, sort_keys=True, default=str)
    return '"' + hashlib.sha256(f"{run_id}|{path}|{query}".encode()).hexdigest()[:32] + '"'


def matches(if_none_match: str | None, tag: str) -> bool:
    if not if_none_match:
        return False
    candidates = {c.strip().removeprefix("W/") for c in if_none_match.split(",")}
    return "*" in candidates or tag in candidates


class ReportCache:
    """Encoded report bodies of one warehouse run; emptied when a newer run succeeds.

    No TTL: a body only changes when the warehouse does. Capped by clearing, which is cheap to
    refill (each report is one query)."""

    def __init__(self, max_entries: int = CACHE_ENTRIES) -> None:
        self.max_entries = max_entries
        self.run_id: str | None = None
        self.entries: dict[str, bytes] = {}

    def get(self, run_id: str, key: str) -> bytes | None:
        if run_id != self.run_id:
            self.entries.clear()
            self.run_id = run_id
        return self.entries.get(key)

    def put(self, key: str, body: bytes) -> None:
        if len(self.entries) >= self.max_entries:
            self.entries.clear()
        self.entries[key] = body


class ReportService:
    def __init__(self, session: AsyncSession, cache: ReportCache) -> None:
        self.session = session
        self.cache = cache

    async def freshness(self, now: datetime) -> ReportFreshness:
        run = await repository.latest_success(self.session)
        status = await repository.latest_status(self.session)
        finished = run.finished_at if run else None
        return ReportFreshness(
            last_run_id=run.run_id if run else None,
            finished_at=finished,
            mode=run.mode if run else None,
            status=status,
            age_s=(now - finished).total_seconds() if finished else None,
        )

    async def run_id(self) -> str:
        """Version of the warehouse data: the latest successful run, "none" before one."""
        run = await repository.latest_success(self.session)
        return str(run.run_id) if run else "none"

    async def check_lot(self, lot_id: str | None) -> None:
        if lot_id is not None and not await repository.lot_exists(self.session, lot_id):
            raise LotNotFound(f"Lot {lot_id!r} does not exist.")

    async def body(
        self,
        run_id: str,
        key: str,
        compute: Callable[[], Awaitable[Sequence[dict[str, Any]]]],
        model: type[BaseModel],
    ) -> bytes:
        """The JSON body of a report, from the cache or computed and validated by `model`."""
        cached = self.cache.get(run_id, key)
        if cached is not None:
            return cached
        rows = [model.model_validate(r).model_dump(mode="json") for r in await compute()]
        body = json.dumps(rows, separators=(",", ":")).encode()
        self.cache.put(key, body)
        return body
