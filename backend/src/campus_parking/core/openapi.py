"""OpenAPI document adjustments that FastAPI cannot express through route declarations."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"


def problem_media_types(schema: dict[str, Any]) -> dict[str, Any]:
    """Document every Problem body under `application/problem+json`, the media type the server
    actually sends (FastAPI files additional-response models under `application/json`)."""
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            for response in operation.get("responses", {}).values():
                content = response.get("content", {})
                body = content.get("application/json")
                if body is not None and body.get("schema", {}).get("$ref") == PROBLEM_REF:
                    content[PROBLEM_MEDIA_TYPE] = content.pop("application/json")
    return schema


def install_openapi(app: FastAPI) -> None:
    """Wrap `app.openapi` so the cached document carries the adjustments above."""
    generate: Callable[[], dict[str, Any]] = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = problem_media_types(generate())
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
