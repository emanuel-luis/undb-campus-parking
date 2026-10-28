"""Prometheus registry and the /metrics endpoint."""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Histogram, generate_latest

registry = CollectorRegistry()

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration by method, route template and status.",
    ["method", "route", "status"],
    registry=registry,
)

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
