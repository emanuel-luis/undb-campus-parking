import json
import logging

import pytest

from campus_parking.core.logging import JsonFormatter, configure_logging, request_id
from campus_parking.core.settings import Settings


def record(**extra: object) -> logging.LogRecord:
    r = logging.LogRecord("x.y", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    for k, v in extra.items():
        setattr(r, k, v)
    return r


def test_json_fields_and_request_id() -> None:
    token = request_id.set("rid-1")
    try:
        out = json.loads(JsonFormatter().format(record(duration_ms=1.5)))
    finally:
        request_id.reset(token)
    assert out["message"] == "hello world"
    assert out["level"] == "INFO"
    assert out["logger"] == "x.y"
    assert out["service"] == "campus-parking"
    assert out["request_id"] == "rid-1"
    assert out["duration_ms"] == 1.5
    assert out["ts"].endswith("+00:00")


def test_no_request_id_is_null() -> None:
    assert json.loads(JsonFormatter().format(record()))["request_id"] is None


def test_exception_is_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        r = record()
        r.exc_info = sys.exc_info()
    assert "ValueError: boom" in json.loads(JsonFormatter().format(r))["exc_info"]


def test_settings_secrets_do_not_leak_into_logs() -> None:
    key = "s" * 32
    settings = Settings(_env_file=None, ingest_api_key=key)  # type: ignore[call-arg]
    line = JsonFormatter().format(record(settings=settings))
    assert key not in line


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_configure_logging(fmt: str) -> None:
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    try:
        configure_logging(Settings(_env_file=None, log_format=fmt, log_level="WARNING"))  # type: ignore[call-arg]
        assert len(root.handlers) == 1
        assert root.level == logging.WARNING
        assert isinstance(root.handlers[0].formatter, JsonFormatter) == (fmt == "json")
    finally:
        root.handlers[:] = saved
        root.setLevel(level)
        logging.getLogger("uvicorn.access").disabled = False
