"""Pure ASGI middlewares: request id plus access log, and HTTP duration metrics."""

import logging
import re
import time
import uuid
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from campus_parking.core import logging as app_logging
from campus_parking.core.metrics import http_request_duration_seconds
from campus_parking.core.problems import unhandled_response

logger = logging.getLogger("campus_parking.access")

REQUEST_ID_HEADER = "X-Request-ID"
_SANE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def route_template(scope: Scope) -> str:
    """Matched route path (bounded cardinality); set by the router while the request runs."""
    route: Any = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        rid = incoming if _SANE_ID.fullmatch(incoming) else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = rid
        token = app_logging.request_id.set(rid)
        status = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "route": route_template(scope),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            app_logging.request_id.reset(token)


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            http_request_duration_seconds.labels(
                scope["method"], route_template(scope), str(status)
            ).observe(time.perf_counter() - started)


class UnhandledErrorMiddleware:
    """Innermost middleware: turns an unhandled exception into the 500 problem response here, so
    CORS and request-id headers still apply. ServerErrorMiddleware stays the last resort."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if started:
                raise
            response = await unhandled_response(Request(scope), exc)
            await response(scope, receive, send)
