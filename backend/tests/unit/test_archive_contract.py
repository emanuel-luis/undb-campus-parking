import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from campus_parking.jobs import archive_contract as c
from campus_parking.jobs.export import MANIFEST, PART, publish_partition

DAY = date(2026, 10, 28)
RUN = UUID(int=99)


def row(n: int, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "event_id": str(UUID(int=n)),
        "gate_id": "principal-entrada",
        "lot_id": "principal",
        "direction": "entry",
        "student_ref": "stu_0123456789ab",
        "occurred_at": datetime(2026, 10, 28, 12, n, tzinfo=UTC),
        "service_date": DAY,
        "received_at": datetime(2026, 10, 28, 12, n, 5, tzinfo=UTC),
        "batch_id": str(UUID(int=1000)),
        "payload_sha256": "a" * 64,
    }
    return {**base, **over}


def table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=c.GATE_EVENTS_SCHEMA)


def test_a_valid_table_passes() -> None:
    c.check_gate_events(table([row(1), row(2)]), DAY, 2)
    c.check_gate_events(table([]), DAY, 0)


def test_a_wrong_schema_fails() -> None:
    wrong = pa.table({"event_id": ["x"]})
    with pytest.raises(c.ArchiveContractError, match="schema"):
        c.check_gate_events(wrong, DAY, 1)
    renamed = table([row(1)]).rename_columns(["a", *c.GATE_EVENTS_SCHEMA.names[1:]])
    with pytest.raises(c.ArchiveContractError, match="schema"):
        c.check_gate_events(renamed, DAY, 1)


def test_a_duplicate_event_id_fails() -> None:
    with pytest.raises(c.ArchiveContractError, match="not unique"):
        c.check_gate_events(table([row(1), row(1)]), DAY, 2)


def test_another_service_date_fails() -> None:
    with pytest.raises(c.ArchiveContractError, match="service_date other than"):
        c.check_gate_events(table([row(1, service_date=date(2026, 10, 27))]), DAY, 1)


def test_a_count_mismatch_fails() -> None:
    with pytest.raises(c.ArchiveContractError, match="source count 3"):
        c.check_gate_events(table([row(1)]), DAY, 3)


def test_a_row_that_breaks_the_row_contract_fails() -> None:
    bad = table([row(1, direction="sideways"), row(2, payload_sha256="zz")])
    with pytest.raises(c.ArchiveContractError, match="2 rows do not validate"):
        c.check_gate_events(bad, DAY, 2)


def test_rejects_check() -> None:
    ok = pa.Table.from_pylist([], schema=c.REJECTS_SCHEMA)
    c.check_rejects(ok, 0)
    with pytest.raises(c.ArchiveContractError, match="source count"):
        c.check_rejects(ok, 1)
    with pytest.raises(c.ArchiveContractError, match="schema"):
        c.check_rejects(pa.table({"a": [1]}), 1)


def test_the_row_digest_follows_the_data_not_the_file() -> None:
    a, b = table([row(1), row(2)]), table([row(1), row(2)])
    assert c.rows_digest(a) == c.rows_digest(b)
    assert c.rows_digest(a) != c.rows_digest(table([row(1), row(2, lot_id="anexo")]))
    assert c.rows_digest(a) != c.rows_digest(table([row(2), row(1)]))


def test_publish_writes_a_manifest_and_a_rerun_is_unchanged(tmp_path: Path) -> None:
    final = tmp_path / "raw" / "gate_events" / "service_date=2026-10-28"
    first = publish_partition(final, "gate_events", RUN, table([row(1), row(2)]), 2)
    assert (first.status, first.rows) == ("written", 2)
    manifest = json.loads((final / MANIFEST).read_text("utf-8"))
    assert manifest["dataset"] == "gate_events"
    assert manifest["schema_version"] == 1
    assert (manifest["row_count"], manifest["source_row_count"]) == (2, 2)
    assert manifest["run_id"] == str(RUN)
    assert manifest["sha256"] == first.sha256
    assert [col["name"] for col in manifest["columns"]] == c.GATE_EVENTS_SCHEMA.names
    assert pq.read_table(final / PART).num_rows == 2

    again = publish_partition(final, "gate_events", UUID(int=100), table([row(1), row(2)]), 2)
    assert (again.status, again.sha256) == ("unchanged", first.sha256)
    assert json.loads((final / MANIFEST).read_text("utf-8"))["run_id"] == str(RUN)

    changed = publish_partition(final, "gate_events", UUID(int=101), table([row(1)]), 1)
    assert changed.status == "written"
    assert pq.read_table(final / PART).num_rows == 1
    # no temp or old directory is left behind
    assert sorted(p.name for p in final.parent.iterdir()) == ["service_date=2026-10-28"]


def test_publish_clears_leftovers_of_a_crashed_run(tmp_path: Path) -> None:
    final = tmp_path / "service_date=2026-10-28"
    leftover = tmp_path / ".service_date=2026-10-28.tmp"
    leftover.mkdir()
    (leftover / "junk").write_text("x", encoding="utf-8")
    publish_partition(final, "gate_events", RUN, table([row(1)]), 1)
    assert not leftover.exists()
