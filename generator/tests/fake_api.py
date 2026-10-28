"""A small in-memory backend behind httpx.MockTransport, speaking the real contract."""

import json
import uuid
from datetime import date

import httpx

from turnstile_generator.api_models import GateEvent

ADMIN, INGEST = "a" * 32, "i" * 32


def problem(status: int, code: str, headers: dict[str, str] | None = None) -> httpx.Response:
    body = {
        "type": f"/problems/{code}",
        "title": code,
        "status": status,
        "detail": f"{code} happened",
        "instance": "/x",
        "code": code,
    }
    return httpx.Response(
        status,
        json=body,
        headers={"content-type": "application/problem+json", **(headers or {})},
    )


class FakeApi:
    def __init__(self, today: date) -> None:
        self.today = today
        self.lots: dict[str, dict] = {}
        self.gates: dict[str, dict] = {}
        self.students: set[str] = set()
        self.events: dict[str, dict] = {}
        self.put_statuses: list[int] = []
        self.batch_sizes: list[int] = []
        self.posted_ids: list[list[str]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _lot_out(self, lot_id: str) -> dict:
        lot = self.lots[lot_id]
        history = lot["capacity_history"]
        current = [
            p["capacity"] for p in history if date.fromisoformat(p["valid_from"]) <= self.today
        ]
        gates = sorted(
            (g for g in self.gates.values() if g["lot_id"] == lot_id), key=lambda g: g["gate_id"]
        )
        return {
            "lot_id": lot_id,
            "name": lot["name"],
            "capacity": current[-1] if current else None,
            "capacity_history": history,
            "gates": gates,
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/v1/lots":
            return httpx.Response(200, json=[self._lot_out(k) for k in sorted(self.lots)])
        key = request.headers.get("x-api-key")
        if path.startswith("/api/v1/admin/") and key != ADMIN:
            return problem(401, "unauthorized")
        if path == "/api/v1/events" and key != INGEST:
            return problem(401, "unauthorized")
        body = json.loads(request.content) if request.content else None
        if path.startswith("/api/v1/admin/lots/"):
            lot_id = path.rsplit("/", 1)[1]
            created = lot_id not in self.lots
            self.lots[lot_id] = body
            self.put_statuses.append(201 if created else 200)
            return httpx.Response(201 if created else 200, json=self._lot_out(lot_id))
        if path.startswith("/api/v1/admin/gates/"):
            gate_id = path.rsplit("/", 1)[1]
            created = gate_id not in self.gates
            self.gates[gate_id] = {"gate_id": gate_id, **body}
            self.put_statuses.append(201 if created else 200)
            return httpx.Response(201 if created else 200, json=self.gates[gate_id])
        if path == "/api/v1/admin/students/bulk" and method == "POST":
            refs = set(body["student_refs"])
            new = refs - self.students
            self.students |= refs
            return httpx.Response(
                200, json={"created": len(new), "unchanged": len(refs) - len(new)}
            )
        if path == "/api/v1/events" and method == "POST":
            return self._ingest(body)
        return httpx.Response(404)

    def _ingest(self, body: list[dict]) -> httpx.Response:
        self.batch_sizes.append(len(body))
        self.posted_ids.append([item["event_id"] for item in body])
        accepted = duplicates = 0
        rejects = []
        for index, item in enumerate(body):
            event = GateEvent.model_validate(item)
            if event.gate_id not in self.gates:
                reason = "unknown_gate"
            elif event.student_ref not in self.students:
                reason = "unknown_student"
            else:
                reason = ""
            if reason:
                rejects.append(
                    {
                        "index": index,
                        "event_id": item["event_id"],
                        "reason": reason,
                        "detail": reason,
                    }
                )
            elif item["event_id"] in self.events:
                duplicates += 1
            else:
                self.events[item["event_id"]] = item
                accepted += 1
        return httpx.Response(
            202,
            json={
                "batch_id": str(uuid.uuid4()),
                "received": len(body),
                "accepted": accepted,
                "duplicates": duplicates,
                "rejected": len(rejects),
                "rejects": rejects,
            },
        )
