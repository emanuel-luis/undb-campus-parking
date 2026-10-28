"""Prometheus registry and the /metrics endpoint."""

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

registry = CollectorRegistry()

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration by method, route template and status.",
    ["method", "route", "status"],
    registry=registry,
)

events_ingested_total = Counter(
    "parking_events_ingested_total", "Gate events accepted, by lot.", ["lot"], registry=registry
)
events_duplicate_total = Counter(
    "parking_events_duplicate_total",
    "Gate events that were already stored.",
    ["lot"],
    registry=registry,
)
events_rejected_total = Counter(
    "parking_events_rejected_total",
    "Gate events rejected, by reason.",
    ["reason"],
    registry=registry,
)
ingest_throttled_total = Counter(
    "parking_ingest_throttled_total",
    "Batches answered 429, by cause (concurrency or lock_timeout).",
    ["cause"],
    registry=registry,
)
event_lag_seconds = Histogram(
    "parking_event_lag_seconds",
    "Seconds between an accepted event's occurred_at and its arrival.",
    buckets=(1, 5, 15, 60, 300, 900, 3600, 6 * 3600, 86400, 7 * 86400, 30 * 86400, 120 * 86400),
    registry=registry,
)

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
