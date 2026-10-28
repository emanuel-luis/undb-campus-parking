"""OpenAPI document adjustments that FastAPI cannot express through route declarations."""

import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from campus_parking.ingestion.contract import GateEvent

PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
HTTP_VALIDATION_REF = "#/components/schemas/HTTPValidationError"


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


def problem_validation_errors(schema: dict[str, Any]) -> dict[str, Any]:
    """FastAPI documents its own 422 body (HTTPValidationError); the server sends a Problem.
    Point every such response at Problem and drop the framework schemas once unused."""
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            response = operation.get("responses", {}).get("422")
            body = (response or {}).get("content", {}).get("application/json", {})
            if body.get("schema", {}).get("$ref") == HTTP_VALIDATION_REF:
                operation["responses"]["422"] = {
                    "description": "Validation error (problem details).",
                    "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": PROBLEM_REF}}},
                }
    components = schema.get("components", {})
    schemas = components.get("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):  # the second is used by the first
        rest = {
            **schema,
            "components": {
                **components,
                "schemas": {k: v for k, v in schemas.items() if k != name},
            },
        }
        if f'"#/components/schemas/{name}"' not in json.dumps(rest):
            schemas.pop(name, None)
    return schema


def register_gate_event(schema: dict[str, Any]) -> dict[str, Any]:
    """Add the GateEvent contract to the components: the events route reads its body raw (item
    errors are rejects, not a 422), so FastAPI never sees the model."""
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas["GateEvent"] = GateEvent.model_json_schema(ref_template="#/components/schemas/{model}")
    return schema


def install_openapi(app: FastAPI) -> None:
    """Wrap `app.openapi` so the cached document carries the adjustments above."""
    generate: Callable[[], dict[str, Any]] = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = register_gate_event(
                problem_media_types(problem_validation_errors(generate()))
            )
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
