from datetime import date, timedelta

import pytest
from conftest import TODAY
from fake_api import FakeApi

from turnstile_generator import registry
from turnstile_generator.backfill import BackfillError, backfill, day_stream
from turnstile_generator.bootstrap import bootstrap
from turnstile_generator.client import ParkingClient
from turnstile_generator.sender import Sender
from turnstile_generator.settings import Settings


def _bootstrap(client, settings, today=TODAY):
    return bootstrap(
        client,
        seed=settings.seed,
        students=settings.students,
        today=today,
        sleeper=lambda s: None,
    )


def test_bootstrap_creates_then_is_idempotent(
    client: ParkingClient, api: FakeApi, settings: Settings
):
    first = _bootstrap(client, settings)
    assert (first.lots_created, first.gates_created) == (2, 4)
    assert first.students_created == 300 and first.students_unchanged == 0
    assert api.put_statuses == [201] * 6
    assert sorted(api.gates) == [
        "anexo-entrada",
        "anexo-saida",
        "principal-entrada",
        "principal-saida",
    ]
    history = api.lots["anexo"]["capacity_history"]
    assert [p["capacity"] for p in history] == [120, 150]
    assert history[1]["valid_from"] == str(TODAY - timedelta(days=45))
    assert api.lots["principal"]["name"] == "Estacionamento Principal"

    api.put_statuses.clear()
    # a later day: the capacity change date is read back, so the history does not move
    second = _bootstrap(client, settings, today=TODAY + timedelta(days=10))
    assert api.put_statuses == [200] * 6
    assert (second.lots_created, second.gates_created) == (0, 0)
    assert second.students_created == 0 and second.students_unchanged == 300
    assert api.lots["anexo"]["capacity_history"] == history


def test_students_are_pseudonymous_and_stable():
    refs = registry.student_refs(42, 10_000)
    assert len(set(refs)) == 10_000
    assert registry.student_refs(42, 3) == refs[:3] != registry.student_refs(43, 3)
    assert all(r.startswith("stu_") and len(r) == 16 for r in refs[:50])


def test_student_upsert_uses_bulk_chunks(client, api, settings):
    _bootstrap(client, settings.model_copy(update={"students": 2500}))
    assert len(api.students) == 2500


def _backfill(client, settings, days=2, end=date(2026, 10, 27), **kw):
    sender = Sender(client, sleeper=lambda s: None)
    return backfill(client, sender, settings, days=days, end_date=end, today=TODAY, **kw)


def test_backfill_posts_in_batches_and_a_rerun_posts_the_same_ids(client, api, settings):
    _bootstrap(client, settings)
    settings = settings.model_copy(update={"backfill_batch_size": 200})
    first = _backfill(client, settings)
    assert first.received > 400 and first.duplicates > 0  # faults inject duplicates
    assert max(api.batch_sizes) == 200 and all(n <= 200 for n in api.batch_sizes)
    assert first.accepted == len(api.events)
    assert sum(first.rejects_by_reason.values()) == first.rejected
    assert set(first.rejects_by_reason) <= {"unknown_student", "unknown_gate"}
    posted = list(api.posted_ids)

    api.posted_ids.clear()
    second = _backfill(client, settings)
    assert api.posted_ids == posted  # deterministic: the same ids in the same batches
    assert second.accepted == 0 and second.rejected == first.rejected
    assert second.duplicates == first.accepted + first.duplicates


def test_backfill_needs_a_bootstrapped_backend(client, settings):
    with pytest.raises(BackfillError, match="bootstrap"):
        _backfill(client, settings)


def test_backfill_refuses_ranges_the_backend_would_reject_as_too_old(client, api, settings):
    _bootstrap(client, settings)
    with pytest.raises(BackfillError, match="older"):
        _backfill(client, settings, days=200)
    with pytest.raises(BackfillError, match="at least"):
        _backfill(client, settings, days=0)


def test_day_stream_is_deterministic(client, api, settings):
    _bootstrap(client, settings)
    specs = registry.specs_from_api(client.get_lots())
    pools = registry.split_students(registry.student_refs(settings.seed, settings.students), specs)
    d = date(2026, 10, 26)
    assert day_stream(settings, specs, pools, d) == day_stream(settings, specs, pools, d)
