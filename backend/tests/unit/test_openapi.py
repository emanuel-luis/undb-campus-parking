"""OpenAPI adjustments: problem bodies are documented with the media type the server sends."""

from typing import Any

from campus_parking.core.openapi import PROBLEM_MEDIA_TYPE, PROBLEM_REF, problem_media_types
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
