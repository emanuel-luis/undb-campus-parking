"""Public availability routes."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.clock import get_now
from campus_parking.core.db import get_session
from campus_parking.core.problems import problem_responses
from campus_parking.occupancy.schemas import AvailabilitySnapshot, LotAvailability
from campus_parking.occupancy.service import OccupancyService
from campus_parking.registry.models import SLUG_PATTERN

router = APIRouter(tags=["availability"])


def get_service(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> OccupancyService:
    return OccupancyService(session, request.app.state.settings.tzinfo)


Service = Annotated[OccupancyService, Depends(get_service)]
Now = Annotated[datetime, Depends(get_now)]


@router.get(
    "/availability", response_model=AvailabilitySnapshot, summary="Availability of every lot"
)
async def availability(service: Service, now: Now) -> AvailabilitySnapshot:
    return await service.snapshot(now)


@router.get(
    "/lots/{lot_id}/availability",
    response_model=LotAvailability,
    summary="Availability of one lot",
    responses=problem_responses(404),
)
async def lot_availability(
    lot_id: Annotated[str, Path(pattern=SLUG_PATTERN)], service: Service, now: Now
) -> LotAvailability:
    return await service.lot(lot_id, now)
