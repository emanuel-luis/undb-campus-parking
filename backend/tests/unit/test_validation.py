import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from campus_parking.ingestion.contract import GateEvent, canonical_json, payload_hash
from campus_parking.ingestion.validation import KnownGate, Reject, validate_item

RECEIVED = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)
GATES = {"p-in": KnownGate("principal", "entry"), "p-out": KnownGate("principal", "exit")}
STUDENTS = {"stu_0123456789ab"}
SKEW, AGE = 300, 120


def raw(**overrides: Any) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "event_id": str(UUID(int=1)),
        "gate_id": "p-in",
        "lot_id": "principal",
        "direction": "entry",
        "student_ref": "stu_0123456789ab",
        "occurred_at": "2026-10-28T13:50:00Z",
    }
    return {**base, **overrides}


def check(item: Any) -> GateEvent | Reject:
    return validate_item(item, GATES, STUDENTS, RECEIVED, SKEW, AGE)


def reason_of(item: Any) -> str:
    result = check(item)
    assert isinstance(result, Reject)
    return result.reason


def test_valid_item_returns_the_event() -> None:
    result = check(raw())
    assert isinstance(result, GateEvent)
    assert result.occurred_at == datetime(2026, 10, 28, 13, 50, tzinfo=UTC)


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (raw(gate_id="ghost"), "unknown_gate"),
        (raw(student_ref="stu_ffffffffffff"), "unknown_student"),
        (raw(lot_id="anexo"), "lot_mismatch"),
        (raw(direction="exit"), "direction_mismatch"),
        (raw(occurred_at="2026-10-28T14:05:01Z"), "in_future"),
        (raw(occurred_at="2026-06-30T13:59:59Z"), "too_old"),
        (raw(schema_version=2), "schema_invalid"),
        (raw(extra="x"), "schema_invalid"),
        (raw(occurred_at="2026-10-28T13:50:00"), "schema_invalid"),
        (raw(student_ref="stu_XYZ"), "schema_invalid"),
        (raw(event_id="nope"), "schema_invalid"),
        ({"gate_id": "p-in"}, "schema_invalid"),
        ([], "schema_invalid"),
        (None, "schema_invalid"),
        (7, "schema_invalid"),
    ],
)
def test_each_reason(item: Any, reason: str) -> None:
    result = check(item)
    assert isinstance(result, Reject)
    assert result.reason == reason
    assert 0 < len(result.detail) <= 200


def test_time_bounds_at_the_edges() -> None:
    edge_future = RECEIVED + timedelta(seconds=SKEW)
    edge_past = RECEIVED - timedelta(days=AGE)
    one = timedelta(microseconds=1)
    for at, ok in [
        (edge_future, True),
        (edge_future + one, False),
        (edge_past, True),
        (edge_past - one, False),
    ]:
        result = check(raw(occurred_at=at.isoformat()))
        assert isinstance(result, GateEvent) is ok, at
    assert reason_of(raw(occurred_at=(edge_future + one).isoformat())) == "in_future"
    assert reason_of(raw(occurred_at=(edge_past - one).isoformat())) == "too_old"


def test_checks_run_in_the_documented_order() -> None:
    item = raw(
        gate_id="ghost",
        student_ref="stu_ffffffffffff",
        lot_id="anexo",
        direction="exit",
        occurred_at="2020-01-01T00:00:00Z",
    )
    fixes = [
        ("unknown_gate", {"gate_id": "p-in"}),
        ("unknown_student", {"student_ref": "stu_0123456789ab"}),
        ("lot_mismatch", {"lot_id": "principal"}),
        ("direction_mismatch", {"direction": "entry"}),
        ("too_old", {"occurred_at": "2026-10-28T13:50:00Z"}),
    ]
    for reason, fix in fixes:
        assert reason_of(item) == reason
        item = {**item, **fix}
    assert isinstance(check(item), GateEvent)
    assert reason_of(raw(gate_id="ghost", schema_version=3)) == "schema_invalid"


def test_reject_carries_the_identifiers_it_could_parse() -> None:
    bad = check(raw(student_ref="stu_XYZ"))
    assert isinstance(bad, Reject)
    assert (bad.event_id, bad.gate_id, bad.lot_id) == (UUID(int=1), "p-in", "principal")
    assert bad.occurred_at is None
    late = check(raw(occurred_at="2026-10-28T20:00:00Z"))
    assert isinstance(late, Reject)
    assert late.occurred_at == datetime(2026, 10, 28, 20, 0, tzinfo=UTC)
    junk = check({"event_id": 5, "gate_id": "g" * 500, "lot_id": 3})
    assert isinstance(junk, Reject)
    assert (junk.event_id, junk.lot_id, len(junk.gate_id or "")) == (None, None, 80)


def test_schema_detail_never_echoes_the_input() -> None:
    result = check(raw(student_ref="stu_SECRETVALUE"))
    assert isinstance(result, Reject)
    assert "SECRETVALUE" not in result.detail
    assert result.detail.startswith("student_ref:")


def test_hash_is_canonical() -> None:
    a = GateEvent.model_validate(raw(occurred_at="2026-10-28T13:50:00Z"))
    b = GateEvent.model_validate(raw(occurred_at="2026-10-28T10:50:00.000-03:00"))
    shuffled = GateEvent.model_validate(dict(reversed(list(raw().items()))))
    assert payload_hash(a) == payload_hash(b) == payload_hash(shuffled)
    assert len(payload_hash(a)) == 64
    text = canonical_json(a)
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    assert json.loads(text)["occurred_at"] == "2026-10-28T13:50:00.000000Z"
    for change in (
        {"student_ref": "stu_ba9876543210"},
        {"direction": "exit"},
        {"occurred_at": "2026-10-28T13:50:00.000001Z"},
        {"gate_id": "p-out"},
        {"event_id": str(UUID(int=2))},
    ):
        assert payload_hash(GateEvent.model_validate(raw(**change))) != payload_hash(a)
