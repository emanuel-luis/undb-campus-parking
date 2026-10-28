"""Operator reads: the rejects list and the ingestion health summary (portable SQL)."""

import base64
import binascii
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.problems import RequestInvalid
from campus_parking.ingestion.models import IngestBatch, IngestReject
from campus_parking.ingestion.schemas import (
    GateHealth,
    IngestionHealth,
    Lag,
    MinuteCount,
    RejectOut,
    RejectPage,
)
from campus_parking.registry.models import Gate


def encode_cursor(received_at: datetime, reject_id: int) -> str:
    raw = f"{received_at.isoformat()}|{reject_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        stamp, _, rid = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        at = datetime.fromisoformat(stamp)
        if at.tzinfo is None:
            raise ValueError("naive timestamp")
        return at, int(rid)
    except (ValueError, binascii.Error) as exc:
        raise RequestInvalid("The cursor is not valid.") from exc


class OperatorService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def rejects(
        self, since: datetime | None, reason: str | None, limit: int, cursor: str | None
    ) -> RejectPage:
        stmt = select(IngestReject).order_by(
            IngestReject.received_at.desc(), IngestReject.reject_id.desc()
        )
        if since is not None:
            stmt = stmt.where(IngestReject.received_at >= since)
        if reason is not None:
            stmt = stmt.where(IngestReject.reason == reason)
        if cursor is not None:
            at, rid = decode_cursor(cursor)
            stmt = stmt.where(
                or_(
                    IngestReject.received_at < at,
                    and_(IngestReject.received_at == at, IngestReject.reject_id < rid),
                )
            )
        rows = list((await self.session.scalars(stmt.limit(limit + 1))).all())
        page = rows[:limit]
        more = len(rows) > limit
        return RejectPage(
            items=[RejectOut.model_validate(r, from_attributes=True) for r in page],
            next_cursor=encode_cursor(page[-1].received_at, page[-1].reject_id) if more else None,
        )

    async def health(self, now: datetime, window_min: int) -> IngestionHealth:
        since = now - timedelta(minutes=window_min)
        batches = (
            await self.session.scalars(select(IngestBatch).where(IngestBatch.received_at >= since))
        ).all()

        buckets: dict[datetime, list[int]] = defaultdict(lambda: [0, 0, 0])
        lag_weight = lag_sum = 0.0
        lag_max: float | None = None
        for b in batches:
            minute = b.received_at.astimezone(UTC).replace(second=0, microsecond=0)
            for slot, n in enumerate((b.accepted, b.duplicates, b.rejected)):
                buckets[minute][slot] += n
            if b.avg_lag_s is not None and b.max_lag_s is not None:
                lag_weight += b.accepted
                lag_sum += b.avg_lag_s * b.accepted
                lag_max = b.max_lag_s if lag_max is None else max(lag_max, b.max_lag_s)

        by_reason = await self.session.execute(
            select(IngestReject.reason, func.count())
            .where(IngestReject.received_at >= since)
            .group_by(IngestReject.reason)
        )
        gates = await self.session.scalars(select(Gate).order_by(Gate.gate_id))
        return IngestionHealth(
            per_minute=[
                MinuteCount(minute=m, accepted=a, duplicates=d, rejected=r)
                for m, (a, d, r) in sorted(buckets.items())
            ],
            rejects_by_reason=dict(sorted(by_reason)),
            lag=Lag(avg_s=lag_sum / lag_weight if lag_weight else None, max_s=lag_max),
            gates=[GateHealth.model_validate(g, from_attributes=True) for g in gates],
            last_batch_at=await self.session.scalar(select(func.max(IngestBatch.received_at))),
        )
