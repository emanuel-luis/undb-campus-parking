"""HTTP surface of ingestion: POST /events (ingest key) and the operator views."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.clock import get_now
from campus_parking.core.db import get_session
from campus_parking.core.metrics import ingest_throttled_total
from campus_parking.core.problems import (
    DatabaseUnavailable,
    IngestBusy,
    RequestInvalid,
    ServiceDraining,
    problem_responses,
)
from campus_parking.core.security import require_ingest_key, require_operator_key
from campus_parking.core.settings import Settings
from campus_parking.ingestion.operator import OperatorService
from campus_parking.ingestion.schemas import IngestionHealth, IngestResult, RejectPage
from campus_parking.ingestion.service import IngestionService, is_lock_timeout
from campus_parking.ingestion.validation import RejectReason

events_router = APIRouter(tags=["ingestion"], dependencies=[Depends(require_ingest_key)])
operator_router = APIRouter(
    prefix="/operator",
    tags=["operator"],
    dependencies=[Depends(require_operator_key)],
    responses=problem_responses(401, 422),
)

Session = Annotated[AsyncSession, Depends(get_session)]
Now = Annotated[datetime, Depends(get_now)]

# Documents the body, which the handler reads raw so that item errors become rejects, not a 422.
# `core/openapi.py` registers the GateEvent component this points at.
EVENTS_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": Settings.model_fields["ingest_max_batch"].default,
                    "items": {"$ref": "#/components/schemas/GateEvent"},
                }
            }
        },
    }
}


async def _read_batch(request: Request, max_items: int) -> list[Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise RequestInvalid("The body is not valid JSON.") from exc
    if not isinstance(body, list):
        raise RequestInvalid("The body must be a JSON array of events.")
    if not 1 <= len(body) <= max_items:
        raise RequestInvalid(f"The array must hold 1 to {max_items} events.")
    return body


@events_router.post(
    "/events",
    status_code=202,
    response_model=IngestResult,
    summary="Ingest a batch of gate events",
    description=(
        "A JSON array of 1 to 1000 GateEvent items. Whole-batch problems are 4xx/5xx; every "
        "item problem is listed in `rejects` of the 202 response and does not fail the batch. "
        "Safe to retry: the same event is a duplicate, never counted twice."
    ),
    responses=problem_responses(401, 422, 429, 503),
    openapi_extra=EVENTS_BODY,
)
async def post_events(request: Request, session: Session, now: Now) -> IngestResult:
    settings: Settings = request.app.state.settings
    if request.app.state.draining:
        raise ServiceDraining()
    batch = await _read_batch(request, settings.ingest_max_batch)
    semaphore = request.app.state.ingest_semaphore
    if semaphore.locked():
        ingest_throttled_total.labels("concurrency").inc()
        raise IngestBusy("Too many batches are being processed; retry shortly.")
    async with semaphore:
        try:
            return await IngestionService(session, settings).ingest(batch, now)
        except DBAPIError as exc:
            if is_lock_timeout(exc):
                ingest_throttled_total.labels("lock_timeout").inc()
                raise IngestBusy("A lot is busy with another batch; retry shortly.") from exc
            if isinstance(exc, OperationalError | InterfaceError):
                raise DatabaseUnavailable() from exc
            raise
        except OSError as exc:
            raise DatabaseUnavailable() from exc


@operator_router.get(
    "/rejects",
    response_model=RejectPage,
    summary="List rejected events",
    description="Newest first. Pass `next_cursor` back as `cursor` for the next page.",
)
async def list_rejects(
    session: Session,
    since: AwareDatetime | None = Query(
        None, description="Only rejects received at or after this time."
    ),
    reason: RejectReason | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> RejectPage:
    return await OperatorService(session).rejects(since, reason, limit, cursor)


@operator_router.get(
    "/ingestion/health",
    response_model=IngestionHealth,
    summary="Ingestion health",
    description="Batches per minute, rejects by reason, arrival lag and per-gate freshness.",
)
async def ingestion_health(
    session: Session, now: Now, window_min: int = Query(60, ge=1, le=240)
) -> IngestionHealth:
    return await OperatorService(session).health(now, window_min)


router = APIRouter()
router.include_router(events_router)
router.include_router(operator_router)
