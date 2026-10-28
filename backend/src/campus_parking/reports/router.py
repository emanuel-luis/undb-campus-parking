"""Public report routes over the warehouse marts: cached per warehouse run, with ETags."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.clock import get_now, service_date
from campus_parking.core.db import get_session
from campus_parking.core.problems import problem_responses
from campus_parking.registry.models import SLUG_PATTERN
from campus_parking.reports import repository
from campus_parking.reports.schemas import (
    DwellBucketRow,
    LotDailyRow,
    LotHourlyRow,
    LotReportSummary,
    OccupancySlot,
    QualityDailyRow,
    ReportFreshness,
    WeekdayRow,
)
from campus_parking.reports.service import (
    MAX_DAYS,
    MAX_HOURLY_DAYS,
    ReportService,
    etag,
    matches,
    resolve_range,
)

router = APIRouter(prefix="/reports", tags=["reports"])

CACHE_CONTROL = "public, max-age=60"


def _range_doc(max_days: int) -> str:
    return (
        f" Range: `from` and `to` (local dates, inclusive; default the last 30 days, at most "
        f"{max_days} days). Without `lot_id`, one series per lot. Cached per warehouse run: send "
        "the `ETag` back in `If-None-Match` for a 304."
    )


RANGE_DOC = _range_doc(MAX_DAYS)
ERRORS = problem_responses(404, 422, 503)


def get_service(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> ReportService:
    return ReportService(session, request.app.state.report_cache)


Service = Annotated[ReportService, Depends(get_service)]
Now = Annotated[datetime, Depends(get_now)]
From = Annotated[date | None, Query(alias="from", description="First local date (inclusive).")]
To = Annotated[date | None, Query(alias="to", description="Last local date (inclusive).")]
LotId = Annotated[str | None, Query(pattern=SLUG_PATTERN, description="Only this lot.")]


async def _respond(
    request: Request,
    service: ReportService,
    params: dict[str, Any],
    compute: Callable[[], Awaitable[Sequence[dict[str, Any]]]],
    model: type[BaseModel],
) -> Response:
    await service.check_lot(params.get("lot_id"))
    run_id = await service.run_id()
    tag = etag(run_id, request.url.path, params)
    headers = {"ETag": tag, "Cache-Control": CACHE_CONTROL}
    if matches(request.headers.get("if-none-match"), tag):
        return Response(status_code=304, headers=headers)
    body = await service.body(run_id, tag, compute, model)
    return Response(body, media_type="application/json", headers=headers)


def _today(request: Request, now: datetime) -> date:
    return service_date(now, request.app.state.settings.tzinfo)


@router.get(
    "/freshness",
    response_model=ReportFreshness,
    summary="Age of the warehouse data",
    description="The latest successful warehouse run and the status of the most recent one.",
)
async def freshness(service: Service, now: Now, response: Response) -> ReportFreshness:
    response.headers["Cache-Control"] = "no-cache"
    return await service.freshness(now)


@router.get(
    "/summary",
    response_model=list[LotReportSummary],
    summary="Totals per lot over a range",
    description="Range-level stay figures are means of the daily figures." + RANGE_DOC,
    responses=ERRORS,
)
async def summary(
    request: Request,
    service: Service,
    now: Now,
    start: From = None,
    end: To = None,
    lot_id: LotId = None,
) -> Response:
    start, end = resolve_range(start, end, _today(request, now))

    async def rows() -> list[dict[str, Any]]:
        found = await repository.summary(service.session, start, end, lot_id)
        return [{**r, "from_date": start, "to_date": end} for r in found]

    params = {"from": start, "to": end, "lot_id": lot_id}
    return await _respond(request, service, params, rows, LotReportSummary)


@router.get(
    "/daily",
    response_model=list[LotDailyRow],
    summary="One row per lot and day",
    description="Traffic, dwell, occupancy and quality counts per lot and day." + RANGE_DOC,
    responses=ERRORS,
)
async def daily(
    request: Request,
    service: Service,
    now: Now,
    start: From = None,
    end: To = None,
    lot_id: LotId = None,
) -> Response:
    start, end = resolve_range(start, end, _today(request, now))
    params = {"from": start, "to": end, "lot_id": lot_id}

    async def rows() -> Sequence[dict[str, Any]]:
        return await repository.daily(service.session, start, end, lot_id)

    return await _respond(request, service, params, rows, LotDailyRow)


@router.get(
    "/hourly",
    response_model=list[LotHourlyRow],
    summary="One row per lot, day and hour",
    description="Hours that have started." + _range_doc(MAX_HOURLY_DAYS),
    responses=ERRORS,
)
async def hourly(
    request: Request,
    service: Service,
    now: Now,
    start: From = None,
    end: To = None,
    lot_id: LotId = None,
) -> Response:
    start, end = resolve_range(start, end, _today(request, now), MAX_HOURLY_DAYS)
    params = {"from": start, "to": end, "lot_id": lot_id}

    async def rows() -> Sequence[dict[str, Any]]:
        return await repository.hourly(service.session, start, end, lot_id)

    return await _respond(request, service, params, rows, LotHourlyRow)


@router.get(
    "/weekday",
    response_model=list[WeekdayRow],
    summary="Daily means per lot and weekday",
    description="Holidays are excluded." + RANGE_DOC,
    responses=ERRORS,
)
async def weekday(
    request: Request,
    service: Service,
    now: Now,
    start: From = None,
    end: To = None,
    lot_id: LotId = None,
) -> Response:
    start, end = resolve_range(start, end, _today(request, now))
    params = {"from": start, "to": end, "lot_id": lot_id}

    async def rows() -> Sequence[dict[str, Any]]:
        return await repository.weekday(service.session, start, end, lot_id)

    return await _respond(request, service, params, rows, WeekdayRow)


@router.get(
    "/occupancy",
    response_model=list[OccupancySlot],
    summary="15-minute occupancy slots of one day",
    description=(
        "Slots of `date` (default today, local) that have started; the current one ends now. "
        "Without `lot_id`, every lot. Cached per warehouse run, with ETags."
    ),
    responses=ERRORS,
)
async def occupancy(
    request: Request,
    service: Service,
    now: Now,
    day: Annotated[date | None, Query(alias="date", description="Local date.")] = None,
    lot_id: LotId = None,
) -> Response:
    day = day or _today(request, now)
    params = {"date": day, "lot_id": lot_id}

    async def rows() -> Sequence[dict[str, Any]]:
        return await repository.occupancy(service.session, day, lot_id)

    return await _respond(request, service, params, rows, OccupancySlot)


@router.get(
    "/dwell",
    response_model=list[DwellBucketRow],
    summary="Closed stays per duration bucket",
    description="All six buckets per lot, summed over the range." + RANGE_DOC,
    responses=ERRORS,
)
async def dwell(
    request: Request,
    service: Service,
    now: Now,
    start: From = None,
    end: To = None,
    lot_id: LotId = None,
) -> Response:
    start, end = resolve_range(start, end, _today(request, now))
    params = {"from": start, "to": end, "lot_id": lot_id}

    async def rows() -> Sequence[dict[str, Any]]:
        return await repository.dwell(service.session, start, end, lot_id)

    return await _respond(request, service, params, rows, DwellBucketRow)


@router.get(
    "/quality",
    response_model=list[QualityDailyRow],
    summary="Data quality per day and lot",
    description="Rejects by reason and reconciled stays; lot '-1' holds rejects without a "
    "known lot." + RANGE_DOC,
    responses=ERRORS,
)
async def quality(
    request: Request,
    service: Service,
    now: Now,
    start: From = None,
    end: To = None,
    lot_id: LotId = None,
) -> Response:
    start, end = resolve_range(start, end, _today(request, now))
    params = {"from": start, "to": end, "lot_id": lot_id}

    async def rows() -> Sequence[dict[str, Any]]:
        return await repository.quality(service.session, start, end, lot_id)

    return await _respond(request, service, params, rows, QualityDailyRow)
