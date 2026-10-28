"""Operator routes for pipeline runs. The API only enqueues; the worker runs the jobs."""

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from campus_parking.core.clock import get_now, service_date
from campus_parking.core.db import get_session
from campus_parking.core.problems import (
    RequestInvalid,
    RunAlreadyActive,
    RunNotFound,
    problem_responses,
)
from campus_parking.core.security import require_operator_key
from campus_parking.core.settings import Settings
from campus_parking.jobs import queue
from campus_parking.jobs.models import PipelineRun
from campus_parking.jobs.schemas import JobName, PipelineRunOut, RunRequest, RunStatus

router = APIRouter(
    prefix="/operator",
    tags=["operator"],
    dependencies=[Depends(require_operator_key)],
    responses=problem_responses(401),
)

Session = Annotated[AsyncSession, Depends(get_session)]
VALID_MODES: dict[str, tuple[str | None, ...]] = {
    "daily": (None,),
    "close_day": (None, "export"),
    "warehouse": (None, "incremental", "full"),
}
Now = Annotated[datetime, Depends(get_now)]


@router.post(
    "/runs",
    status_code=202,
    response_model=PipelineRunOut,
    summary="Queue a pipeline run",
    description=(
        "Queues `daily` or `close_day` for a past service date (default: yesterday), or "
        "`warehouse` for today or a past date (default: today). The worker "
        "picks it up; poll `GET /operator/runs/{run_id}`. 409 when one is already queued or "
        "running for the same job and date."
    ),
    responses=problem_responses(409, 422),
)
async def post_run(request: Request, body: RunRequest, session: Session, now: Now) -> PipelineRun:
    settings: Settings = request.app.state.settings
    today = service_date(now, settings.tzinfo)
    if body.job == "warehouse":  # the date only labels the run: today or a past date
        day = body.service_date or today
        if day > today:
            raise RequestInvalid(f"service_date must not be after {today} for warehouse.")
    else:
        day = body.service_date or today - timedelta(days=1)
        if day >= today:
            raise RequestInvalid(
                f"service_date must be before {today} (the service time zone's today)."
            )
    if body.mode not in VALID_MODES[body.job]:
        raise RequestInvalid(f"mode {body.mode!r} is not valid for job {body.job}.")
    mode = "incremental" if body.job == "warehouse" and body.mode is None else body.mode
    run, created = await queue.enqueue(session, body.job, day, "operator", now, mode)
    if not created:
        raise RunAlreadyActive(f"Run {run.run_id} ({run.status}) already covers {body.job} {day}.")
    return run


@router.get(
    "/runs",
    response_model=list[PipelineRunOut],
    summary="List pipeline runs",
    description="Newest first.",
    responses=problem_responses(422),
)
async def list_runs(
    session: Session,
    job: JobName | None = None,
    status: RunStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> list[PipelineRunOut]:
    rows = await queue.list_runs(session, job, status, limit)
    return [PipelineRunOut.model_validate(r, from_attributes=True) for r in rows]


@router.get(
    "/runs/{run_id}",
    response_model=PipelineRunOut,
    summary="One pipeline run",
    responses=problem_responses(404, 422),
)
async def get_run(run_id: UUID, session: Session) -> PipelineRunOut:
    run = await session.get(PipelineRun, run_id)
    if run is None:
        raise RunNotFound(f"Run {run_id} does not exist.")
    return PipelineRunOut.model_validate(run, from_attributes=True)
