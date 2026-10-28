"""OpenAPI adjustments: problem bodies are documented with the media type the server sends."""

from typing import Any

from campus_parking.core.openapi import (
    PROBLEM_MEDIA_TYPE,
    PROBLEM_REF,
    problem_media_types,
    problem_validation_errors,
)
from campus_parking.main import create_app
from tests.conftest import make_settings


def _problem_bodies(schema: dict[str, Any]) -> list[tuple[str, str, str, list[str]]]:
    found = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for status, response in operation["responses"].items():
                content = response.get("content", {})
                if any(c.get("schema", {}).get("$ref") == PROBLEM_REF for c in content.values()):
                    found.append((path, method, status, sorted(content)))
    return found


def test_every_problem_body_is_problem_json() -> None:
    bodies = _problem_bodies(create_app(make_settings()).openapi())
    assert ("/readyz", "get", "503", [PROBLEM_MEDIA_TYPE]) in bodies
    assert ("/api/v1/admin/gates/{gate_id}", "put", "409", [PROBLEM_MEDIA_TYPE]) in bodies
    assert all(media == [PROBLEM_MEDIA_TYPE] for *_, media in bodies)


def test_other_bodies_are_left_alone() -> None:
    schema = {
        "paths": {
            "/x": {
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object"}}}},
                        "404": {"content": {"application/json": {"schema": {"$ref": PROBLEM_REF}}}},
                        "204": {"description": "empty"},
                    }
                }
            }
        }
    }
    responses = problem_media_types(schema)["paths"]["/x"]["get"]["responses"]
    assert list(responses["200"]["content"]) == ["application/json"]
    assert list(responses["404"]["content"]) == [PROBLEM_MEDIA_TYPE]
    assert "content" not in responses["204"]


def test_document_is_cached() -> None:
    app = create_app(make_settings())
    assert app.openapi() is app.openapi()


def test_default_422_bodies_become_problems() -> None:
    doc = create_app(make_settings()).openapi()
    for path, method in [
        ("/api/v1/lots/{lot_id}/availability", "get"),
        ("/api/v1/operator/rejects", "get"),
        ("/api/v1/operator/ingestion/health", "get"),
    ]:
        response = doc["paths"][path][method]["responses"]["422"]
        assert response["content"] == {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": PROBLEM_REF}}}
    assert "HTTPValidationError" not in doc["components"]["schemas"]
    assert "ValidationError" not in doc["components"]["schemas"]
    assert "HTTPValidationError" not in str(doc)


def test_framework_schemas_stay_while_something_still_uses_them() -> None:
    ref = "#/components/schemas/HTTPValidationError"
    schema = {
        "paths": {
            "/a": {
                "get": {
                    "responses": {
                        "422": {"content": {"application/json": {"schema": {"$ref": ref}}}}
                    }
                }
            },
            "/b": {
                "get": {
                    "responses": {
                        "400": {"content": {"application/json": {"schema": {"$ref": ref}}}}
                    }
                }
            },
        },
        "components": {
            "schemas": {
                "HTTPValidationError": {
                    "properties": {"d": {"items": {"$ref": "#/components/schemas/ValidationError"}}}
                },
                "ValidationError": {"type": "object"},
            }
        },
    }
    out = problem_validation_errors(schema)
    assert list(out["paths"]["/a"]["get"]["responses"]["422"]["content"]) == [PROBLEM_MEDIA_TYPE]
    assert set(out["components"]["schemas"]) == {"HTTPValidationError", "ValidationError"}
