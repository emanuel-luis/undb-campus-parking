"""Liveness and readiness probes."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from campus_parking.core.problems import DatabaseUnavailable, ServiceDraining

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: Literal["ready"] = "ready"
    database: Literal["ok"] = "ok"


@router.get("/healthz", response_model=HealthStatus)
async def healthz() -> HealthStatus:
    return HealthStatus()


@router.get(
    "/readyz",
    response_model=Readiness,
    responses={503: {"description": "Draining or database unavailable (problem+json)."}},
)
async def readyz(request: Request) -> Readiness:
    if request.app.state.draining:
        raise ServiceDraining()
    try:
        async with asyncio.timeout(request.app.state.settings.readiness_timeout_s):
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        raise DatabaseUnavailable() from exc
    return Readiness()
