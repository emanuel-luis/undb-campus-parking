"""Application factory. No work happens at import time: run with `uvicorn --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from campus_parking.core import health, metrics
from campus_parking.core.db import create_engine, create_sessionmaker
from campus_parking.core.middleware import MetricsMiddleware, RequestIdMiddleware
from campus_parking.core.problems import register_problem_handlers
from campus_parking.core.settings import Settings, get_settings

TITLE = "UNDB Campus Parking API"
TAGS = [{"name": "health", "description": "Liveness and readiness probes."}]

api_v1 = APIRouter(prefix="/api/v1")


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
        app.state.draining = False
        try:
            yield
        finally:
            app.state.draining = True
            await app.state.engine.dispose()

    app = FastAPI(title=TITLE, version=package_version(), openapi_tags=TAGS, lifespan=lifespan)
    app.state.settings = settings
    app.state.draining = False

    # The last middleware added is the outermost one.
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
    app.include_router(api_v1)
    return app
