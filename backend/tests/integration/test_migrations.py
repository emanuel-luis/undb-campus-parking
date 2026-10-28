"""Alembic against a real Postgres. Sync tests: alembic's env.py starts its own event loop."""

import asyncio

from alembic import command
from campus_parking.cli import alembic_config
from tests.integration.conftest import fetch_column

TABLES = ["gates", "lot_capacity_history", "lots", "students"]
TABLES_SQL = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' ORDER BY table_name"
)


def test_upgrade_downgrade_upgrade(pg_database: str) -> None:
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    assert asyncio.run(fetch_column(pg_database, TABLES_SQL)) == ["alembic_version", *TABLES]
    command.downgrade(cfg, "base")
    assert asyncio.run(fetch_column(pg_database, TABLES_SQL)) == ["alembic_version"]
    command.upgrade(cfg, "head")
    assert asyncio.run(fetch_column(pg_database, TABLES_SQL)) == ["alembic_version", *TABLES]


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
    } <= set(names)
