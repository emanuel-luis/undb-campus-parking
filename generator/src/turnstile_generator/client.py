"""Thin httpx client over the backend API, typed with the generated models."""

from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import SecretStr, TypeAdapter, ValidationError

from turnstile_generator.api_models import (
    BulkUpsertResult,
    CapacityPeriod,
    GateEvent,
    GateOut,
    GateUpsert,
    IngestResult,
    LotOut,
    LotUpsert,
    Problem,
    StudentBulkUpsert,
)

_EVENTS = TypeAdapter(list[GateEvent])
_LOTS = TypeAdapter(list[LotOut])
MAX_RETRY_AFTER_S = 60.0


class ApiError(Exception):
    """The backend answered with an error status."""

    def __init__(self, status: int, problem: Problem | None, retry_after: float | None) -> None:
        self.status = status
        self.problem = problem
        self.retry_after = retry_after
        detail = f"{problem.code}: {problem.detail}" if problem else "no problem body"
        super().__init__(f"HTTP {status} ({detail})")


class FatalApiError(ApiError):
    """A 4xx other than 429: a wrong key, a rejected batch or a bug. Retrying cannot help."""


class RetryableApiError(ApiError):
    """429 or 5xx: the backend is busy, draining or down."""


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return min(max(float(response.headers["Retry-After"]), 0.0), MAX_RETRY_AFTER_S)
    except (KeyError, ValueError):
        return None


def _check(response: httpx.Response) -> httpx.Response:
    if response.is_success:
        return response
    try:
        problem: Problem | None = Problem.model_validate_json(response.content)
    except ValidationError:
        problem = None
    cls = (
        RetryableApiError
        if response.status_code == 429 or response.status_code >= 500
        else FatalApiError
    )
    raise cls(response.status_code, problem, _retry_after(response))


class ParkingClient:
    def __init__(
        self,
        base_url: str,
        *,
        ingest_key: SecretStr | None = None,
        admin_key: SecretStr | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)
        self._ingest = ingest_key
        self._admin = admin_key

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, url: str, key: SecretStr | None, **kw: Any) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-API-Key"] = key.get_secret_value()
        return _check(self._http.request(method, url, headers=headers, **kw))

    def get_lots(self) -> list[LotOut]:
        return _LOTS.validate_json(self._request("GET", "/api/v1/lots", None).content)

    def put_lot(
        self, lot_id: str, name: str, history: Sequence[CapacityPeriod]
    ) -> tuple[LotOut, bool]:
        body = LotUpsert(name=name, capacity_history=list(history))
        r = self._request("PUT", f"/api/v1/admin/lots/{lot_id}", self._admin, content=_json(body))
        return LotOut.model_validate_json(r.content), r.status_code == 201

    def put_gate(self, gate_id: str, body: GateUpsert) -> tuple[GateOut, bool]:
        r = self._request("PUT", f"/api/v1/admin/gates/{gate_id}", self._admin, content=_json(body))
        return GateOut.model_validate_json(r.content), r.status_code == 201

    def upsert_students(self, refs: list[str]) -> BulkUpsertResult:
        body = StudentBulkUpsert.model_validate({"student_refs": refs})
        r = self._request("POST", "/api/v1/admin/students/bulk", self._admin, content=_json(body))
        return BulkUpsertResult.model_validate_json(r.content)

    def post_events(self, events: Sequence[GateEvent]) -> IngestResult:
        content = _EVENTS.dump_json(list(events))
        r = self._request("POST", "/api/v1/events", self._ingest, content=content)
        return IngestResult.model_validate_json(r.content)


def _json(model: LotUpsert | GateUpsert | StudentBulkUpsert) -> bytes:
    return model.model_dump_json().encode()
