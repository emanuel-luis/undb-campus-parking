"""Alembic against a real Postgres. Sync tests: alembic's env.py starts its own event loop."""

import asyncio

from alembic import command
from campus_parking.cli import alembic_config
from tests.integration.conftest import fetch_column, run_sql

TABLES_0001 = ["gates", "lot_capacity_history", "lots", "students"]
TABLES_0002 = [
    "gate_events",
    "gates",
    "ingest_batches",
    "ingest_rejects",
    "lot_capacity_history",
    "lot_occupancy",
    "lots",
    "stays",
    "students",
]
TABLES = sorted([*TABLES_0002, "pipeline_runs", "service_days"])
TABLES_SQL = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' ORDER BY table_name"
)

TRIGGER_SQL = (
    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
    "UNION ALL SELECT proname FROM pg_proc WHERE proname = 'gate_events_append_only'"
)


def test_upgrade_downgrade_upgrade(pg_database: str) -> None:
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    assert asyncio.run(fetch_column(pg_database, TABLES_SQL)) == ["alembic_version", *TABLES]
    command.downgrade(cfg, "0002")
    assert asyncio.run(fetch_column(pg_database, TABLES_SQL)) == [
        "alembic_version",
        *TABLES_0002,
    ]
    command.downgrade(cfg, "0001")
    assert asyncio.run(fetch_column(pg_database, TABLES_SQL)) == ["alembic_version", *TABLES_0001]
    assert asyncio.run(fetch_column(pg_database, TRIGGER_SQL)) == []
    command.downgrade(cfg, "base")
    assert asyncio.run(fetch_column(pg_database, TABLES_SQL)) == ["alembic_version"]
    command.upgrade(cfg, "head")
    assert asyncio.run(fetch_column(pg_database, TABLES_SQL)) == ["alembic_version", *TABLES]
    assert sorted(asyncio.run(fetch_column(pg_database, TRIGGER_SQL))) == [
        "gate_events_append_only",
        "trg_gate_events_append_only",
    ]


def test_existing_lots_get_a_counter_row(pg_database: str) -> None:
    cfg = alembic_config()
    command.upgrade(cfg, "0001")
    asyncio.run(
        run_sql(
            pg_database,
            "INSERT INTO lots VALUES ('old', 'Old', now(), now()), ('older', 'O', now(), now())",
        )
    )
    command.upgrade(cfg, "head")
    rows = asyncio.run(
        fetch_column(
            pg_database,
            "SELECT lot_id || ':' || occupied || ':' || version FROM lot_occupancy ORDER BY 1",
        )
    )
    assert rows == ["old:0:0", "older:0:0"]


def test_models_match_migrations(pg_database: str) -> None:
    """`alembic check` raises when the autogenerate diff is not empty."""
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    command.check(cfg)


def test_constraint_names_follow_the_convention(pg_database: str) -> None:
    command.upgrade(alembic_config(), "head")
    names = asyncio.run(
        fetch_column(
            pg_database,
            "SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace",
        )
    )
    assert {
        "pk_lots",
        "pk_lot_capacity_history",
        "pk_gates",
        "pk_students",
        "fk_lot_capacity_history_lot_id_lots",
        "fk_gates_lot_id_lots",
        "ck_lot_capacity_history_capacity_positive",
        "ck_gates_direction_valid",
        "ck_students_student_ref_format",
        "pk_ingest_batches",
        "pk_gate_events",
        "pk_ingest_rejects",
        "pk_stays",
        "pk_lot_occupancy",
        "fk_gate_events_batch_id_ingest_batches",
        "fk_gate_events_gate_id_gates",
        "fk_gate_events_lot_id_lots",
        "fk_gate_events_student_ref_students",
        "fk_ingest_rejects_batch_id_ingest_batches",
        "fk_lot_occupancy_lot_id_lots",
        "ck_ingest_rejects_reason_valid",
        "ck_stays_status_valid",
        "ck_lot_occupancy_occupied_non_negative",
        "uq_stays_entry_event_id",
        "uq_stays_exit_event_id",
        "pk_pipeline_runs",
        "pk_service_days",
        "fk_pipeline_runs_parent_run_id_pipeline_runs",
        "fk_service_days_run_id_pipeline_runs",
        "ck_pipeline_runs_job_valid",
        "ck_pipeline_runs_trigger_valid",
        "ck_pipeline_runs_status_valid",
    } <= set(names)


def test_index_names_and_the_partial_index(pg_database: str) -> None:
    command.upgrade(alembic_config(), "head")
    defs = asyncio.run(
        fetch_column(
            pg_database,
            "SELECT indexname || ' ' || indexdef FROM pg_indexes WHERE schemaname = 'public'",
        )
    )
    names = {d.split()[0] for d in defs}
    assert {
        "ix_gate_events_lot_id_student_ref_service_date",
        "ix_gate_events_service_date_lot_id",
        "ix_gate_events_received_at",
        "ix_ingest_batches_received_at",
        "ix_ingest_rejects_received_at",
        "ix_stays_lot_id_student_ref_service_date",
        "ix_stays_updated_at",
        "ix_stays_lot_id_open",
        "uq_pipeline_runs_job_service_date_active",
        "ix_pipeline_runs_requested_at_queued",
    } <= names
    partial = next(d for d in defs if d.startswith("ix_stays_lot_id_open"))
    assert "WHERE (status = 'open'::text)" in partial
    active = next(d for d in defs if d.startswith("uq_pipeline_runs_job_service_date_active"))
    assert "UNIQUE INDEX" in active
    assert "'queued'::text" in active
    assert "'running'::text" in active
    queued = next(d for d in defs if d.startswith("ix_pipeline_runs_requested_at_queued"))
    assert "WHERE (status = 'queued'::text)" in queued
