"""RFC 9457 problem details and the one place where errors become HTTP responses."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from campus_parking.core import logging as app_logging

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"
REQUEST_ID_HEADER = "X-Request-ID"


class Problem(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str | None = None
    errors: list[dict[str, Any]] | None = None


class DomainError(Exception):
    """Base for errors the API reports as problem details."""

    code = "internal-error"
    status = 500
    title = "Internal error"

    def __init__(self, detail: str | None = None, *, headers: dict[str, str] | None = None) -> None:
        self.detail = detail or self.title
        self.headers = headers or {}
        super().__init__(self.detail)


class Unauthorized(DomainError):
    code = "unauthorized"
    status = 401
    title = "Unauthorized"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail, headers={"WWW-Authenticate": "ApiKey"})


class LotNotFound(DomainError):
    code = "lot-not-found"
    status = 404
    title = "Lot not found"


class GateImmutable(DomainError):
    code = "gate-immutable"
    status = 409
    title = "Gate lot and direction cannot change"


class ServiceDraining(DomainError):
    code = "service-draining"
    status = 503
    title = "Service is shutting down"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail, headers={"Retry-After": "5"})


class DatabaseUnavailable(DomainError):
    code = "database-unavailable"
    status = 503
    title = "Database unavailable"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail, headers={"Retry-After": "5"})


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` entries documenting problem+json bodies for the given statuses."""
    return {s: {"model": Problem, "description": "Problem details (RFC 9457)."} for s in statuses}


def _request_id(request: Request) -> str | None:
    return app_logging.request_id.get() or getattr(request.state, "request_id", None)


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    rid = _request_id(request)
    body = Problem(
        type=f"/problems/{code}",
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        code=code,
        request_id=rid,
        errors=errors,
    )
    out = dict(headers or {})
    if rid:
        out[REQUEST_ID_HEADER] = rid
    return JSONResponse(
        body.model_dump(exclude_none=True), status_code=status, media_type=PROBLEM_JSON, headers=out
    )


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return problem_response(
        request,
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        headers=exc.headers,
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return problem_response(
        request,
        status=422,
        code="validation-error",
        title="Validation error",
        detail="The request is not valid.",
        errors=errors,
    )


async def _http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    codes = {404: "not-found", 405: "method-not-allowed"}
    code = codes.get(exc.status_code, f"http-{exc.status_code}")
    return problem_response(
        request,
        status=exc.status_code,
        code=code,
        title=code.replace("-", " ").capitalize(),
        detail=str(exc.detail),
        headers=dict(exc.headers or {}),
    )


async def unhandled_response(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled exception",
        exc_info=exc,
        extra={"path": request.url.path, "request_id": _request_id(request)},
    )
    return problem_response(
        request,
        status=500,
        code="internal-error",
        title="Internal error",
        detail="An unexpected error occurred.",
    )


def register_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(Exception, unhandled_response)
