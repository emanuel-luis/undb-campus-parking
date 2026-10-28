"""Shared builders for the ingestion tests: a seeded registry and gate-event payloads."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

ADMIN = {"X-API-Key": "a" * 32}
INGEST = {"X-API-Key": "i" * 32}
OPERATOR = {"X-API-Key": "o" * 32}
# 2026-10-28 14:00 UTC is 11:00 on 2026-10-28 in America/Fortaleza (UTC-3).
NOW = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)
REF_A = "stu_0123456789ab"
REF_B = "stu_ba9876543210"
REF_C = "stu_00000000000c"
GATES = {
    "principal-entrada": ("principal", "entry"),
    "principal-saida": ("principal", "exit"),
    "anexo-entrada": ("anexo", "entry"),
    "anexo-saida": ("anexo", "exit"),
}


def iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def event(
    n: int,
    direction: str = "entry",
    student: str = REF_A,
    ago_min: float = 10,
    lot: str = "principal",
    **overrides: Any,
) -> dict[str, Any]:
    """A valid GateEvent for the gate of `lot` and `direction`, `ago_min` minutes before NOW."""
    body = {
        "schema_version": 1,
        "event_id": str(UUID(int=n)),
        "gate_id": f"{lot}-{'entrada' if direction == 'entry' else 'saida'}",
        "lot_id": lot,
        "direction": direction,
        "student_ref": student,
        "occurred_at": iso(NOW - timedelta(minutes=ago_min)),
    }
    return {**body, **overrides}


async def seed_registry(client: httpx.AsyncClient, capacity: int = 3) -> None:
    for lot in ("principal", "anexo"):
        body = {
            "name": lot.title(),
            "capacity_history": [{"valid_from": "2026-01-01", "capacity": capacity}],
        }
        assert (
            await client.put(f"/api/v1/admin/lots/{lot}", json=body, headers=ADMIN)
        ).status_code == 201
    for gate_id, (lot, direction) in GATES.items():
        body = {"lot_id": lot, "direction": direction, "name": gate_id}
        assert (
            await client.put(f"/api/v1/admin/gates/{gate_id}", json=body, headers=ADMIN)
        ).status_code == 201
    refs = {"student_refs": [REF_A, REF_B, REF_C]}
    assert (
        await client.post("/api/v1/admin/students/bulk", json=refs, headers=ADMIN)
    ).status_code == 200
