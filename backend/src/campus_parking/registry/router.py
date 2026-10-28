"""HTTP surface of the registry: public GET /lots and the admin writes."""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.clock import get_now, service_date
from campus_parking.core.db import get_session
from campus_parking.core.problems import problem_responses
from campus_parking.core.security import require_admin_key
from campus_parking.core.settings import Settings
from campus_parking.registry.models import SLUG_PATTERN
from campus_parking.registry.repository import RegistryRepository
from campus_parking.registry.schemas import (
    BulkUpsertResult,
    GateOut,
    GateUpsert,
    LotOut,
    LotUpsert,
    StudentBulkUpsert,
)
from campus_parking.registry.service import RegistryService

public_router = APIRouter(tags=["lots"])
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_key)],
    responses=problem_responses(401, 422),
)

SlugPath = Annotated[str, Path(pattern=SLUG_PATTERN)]
Now = Annotated[datetime, Depends(get_now)]


def get_service(session: Annotated[AsyncSession, Depends(get_session)]) -> RegistryService:
    return RegistryService(RegistryRepository(session))


Service = Annotated[RegistryService, Depends(get_service)]


def _today(request: Request, now: datetime) -> date:
    settings: Settings = request.app.state.settings
    return service_date(now, settings.tzinfo)


@public_router.get("/lots", response_model=list[LotOut], summary="List lots")
async def list_lots(request: Request, service: Service, now: Now) -> list[LotOut]:
    return await service.list_lots(_today(request, now))


@admin_router.put(
    "/lots/{lot_id}",
    response_model=LotOut,
    summary="Create or replace a lot",
    description=(
        "Replaces the lot's name and its whole capacity history; the same body twice leaves the "
        "same state. 201 when the lot was created, 200 when it existed."
    ),
    responses={201: {"model": LotOut, "description": "Lot created."}},
)
async def put_lot(
    lot_id: SlugPath,
    body: LotUpsert,
    request: Request,
    response: Response,
    service: Service,
    now: Now,
) -> LotOut:
    lot, created = await service.upsert_lot(lot_id, body, now, _today(request, now))
    if created:
        response.status_code = 201
    return lot


@admin_router.put(
    "/gates/{gate_id}",
    response_model=GateOut,
    summary="Create or rename a gate",
    description=(
        "A gate's lot and direction are fixed from creation; only the name may change. "
        "201 when created, 200 when it existed."
    ),
    responses={
        201: {"model": GateOut, "description": "Gate created."},
        **problem_responses(404, 409),
    },
)
async def put_gate(
    gate_id: SlugPath, body: GateUpsert, response: Response, service: Service, now: Now
) -> GateOut:
    gate, created = await service.upsert_gate(gate_id, body, now)
    if created:
        response.status_code = 201
    return gate


@admin_router.post(
    "/students/bulk",
    response_model=BulkUpsertResult,
    summary="Register students in bulk",
    description="Insert-ignore: known refs are left as they are. Repeats in the body count once.",
)
async def bulk_students(body: StudentBulkUpsert, service: Service, now: Now) -> BulkUpsertResult:
    return await service.bulk_students(body.student_refs, now)


router = APIRouter()
router.include_router(public_router)
router.include_router(admin_router)
