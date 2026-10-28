"""Application factory. No work happens at import time: run with `uvicorn --factory`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from campus_parking.core import health, metrics
from campus_parking.core.db import create_engine, create_sessionmaker
from campus_parking.core.middleware import (
    MetricsMiddleware,
    RequestIdMiddleware,
    UnhandledErrorMiddleware,
)
from campus_parking.core.openapi import install_openapi
from campus_parking.core.problems import register_problem_handlers
from campus_parking.core.settings import Settings, get_settings
from campus_parking.ingestion.router import router as ingestion_router
from campus_parking.jobs.router import router as jobs_router
from campus_parking.occupancy.router import router as occupancy_router
from campus_parking.realtime.hub import Hub
from campus_parking.realtime.poller import DbSource, Poller
from campus_parking.realtime.router import router as realtime_router
from campus_parking.registry.router import router as registry_router

TITLE = "UNDB Campus Parking API"
TAGS = [
    {"name": "health", "description": "Liveness and readiness probes."},
    {"name": "lots", "description": "Public lot registry: capacity and gates."},
    {"name": "admin", "description": "Registry writes (admin API key)."},
    {"name": "availability", "description": "Free spaces per lot, derived from gate events."},
    {"name": "ingestion", "description": "Gate-event intake (ingest API key)."},
    {
        "name": "operator",
        "description": "Ingestion health, rejects and pipeline runs (operator API key).",
    },
]


def package_version() -> str:
    try:
        return version("campus-parking")
    except PackageNotFoundError:  # not installed, e.g. run from a bare checkout
        return "0.0.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(settings)
        app.state.sessionmaker = create_sessionmaker(app.state.engine)
        app.state.hub = Hub(settings.live_max_connections)
        app.state.live_source = DbSource(app.state.sessionmaker, settings.tzinfo)
        poller = Poller(
            app.state.live_source,
            app.state.hub,
            settings.live_poll_interval_s,
            settings.live_heartbeat_s,
        )
        poll_task = asyncio.create_task(poller.run())
        try:
            yield
        finally:
            app.state.draining = True
            await app.state.hub.close()  # sockets leave with 1001
            poll_task.cancel()
            await asyncio.gather(poll_task, return_exceptions=True)
            await app.state.engine.dispose()

    app = FastAPI(title=TITLE, version=package_version(), openapi_tags=TAGS, lifespan=lifespan)
    app.state.settings = settings
    app.state.draining = False
    # Per-process cap on batches in flight; the events route never waits for it.
    app.state.ingest_semaphore = asyncio.Semaphore(settings.ingest_max_concurrency)

    # The last middleware added is the outermost one.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        allow_credentials=False,
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestIdMiddleware)
    register_problem_handlers(app)

    app.include_router(health.router)
    app.include_router(metrics.router)
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(registry_router)
    api_v1.include_router(ingestion_router)
    api_v1.include_router(jobs_router)
    api_v1.include_router(occupancy_router)
    api_v1.include_router(realtime_router)
    app.include_router(api_v1)
    install_openapi(app)
    return app
