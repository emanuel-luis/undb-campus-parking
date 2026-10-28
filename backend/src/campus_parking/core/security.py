"""Three API-key dependencies on the X-API-Key header (not wired to routes yet)."""

import secrets
from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader
from pydantic import SecretStr

from campus_parking.core.problems import Unauthorized
from campus_parking.core.settings import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _check(provided: str | None, expected: SecretStr | None) -> None:
    if expected is None or not provided:
        raise Unauthorized("A valid X-API-Key header is required.")
    if not secrets.compare_digest(provided.encode(), expected.get_secret_value().encode()):
        raise Unauthorized("A valid X-API-Key header is required.")


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _dependency(attr: str) -> Callable[..., Awaitable[None]]:
    async def dependency(request: Request, provided: str | None = Depends(api_key_header)) -> None:
        _check(provided, getattr(_settings(request), attr))

    dependency.__name__ = f"require_{attr.removesuffix('_api_key')}_key"
    return dependency


require_ingest_key = _dependency("ingest_api_key")
require_admin_key = _dependency("admin_api_key")
require_operator_key = _dependency("operator_api_key")
