"""Ingestion and occupancy: batches, events, rejects, stays, lot_occupancy, gates.last_*.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

OPEN = sa.text("status = 'open'")


def upgrade() -> None:
    op.add_column("gates", sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("gates", sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "ingest_batches",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("items", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("avg_lag_s", sa.Float(), nullable=True),
        sa.Column("max_lag_s", sa.Float(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("batch_id", name=op.f("pk_ingest_batches")),
    )
    op.create_index("ix_ingest_batches_received_at", "ingest_batches", ["received_at"])

    op.create_table(
        "gate_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("gate_id", sa.Text(), nullable=False),
        sa.Column("lot_id", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("student_ref", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["ingest_batches.batch_id"],
            name=op.f("fk_gate_events_batch_id_ingest_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["gate_id"], ["gates.gate_id"], name=op.f("fk_gate_events_gate_id_gates")
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"], ["lots.lot_id"], name=op.f("fk_gate_events_lot_id_lots")
        ),
        sa.ForeignKeyConstraint(
            ["student_ref"],
            ["students.student_ref"],
            name=op.f("fk_gate_events_student_ref_students"),
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_gate_events")),
    )
    op.create_index(
        "ix_gate_events_lot_id_student_ref_service_date",
        "gate_events",
        ["lot_id", "student_ref", "service_date"],
    )
    op.create_index("ix_gate_events_service_date_lot_id", "gate_events", ["service_date", "lot_id"])
    op.create_index("ix_gate_events_received_at", "gate_events", ["received_at"])

    op.create_table(
        "ingest_rejects",
        sa.Column(
            "reject_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=True),
        sa.Column("lot_id", sa.Text(), nullable=True),
        sa.Column("gate_id", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reason IN ('schema_invalid', 'unknown_gate', 'unknown_student', 'lot_mismatch', "
            "'direction_mismatch', 'in_future', 'too_old', 'event_id_conflict')",
            name=op.f("ck_ingest_rejects_reason_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["ingest_batches.batch_id"],
            name=op.f("fk_ingest_rejects_batch_id_ingest_batches"),
        ),
        sa.PrimaryKeyConstraint("reject_id", name=op.f("pk_ingest_rejects")),
    )
    op.create_index("ix_ingest_rejects_received_at", "ingest_rejects", ["received_at"])

    op.create_table(
        "stays",
        sa.Column("stay_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Text(), nullable=False),
        sa.Column("student_ref", sa.Text(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("entry_event_id", sa.Uuid(), nullable=True),
        sa.Column("exit_event_id", sa.Uuid(), nullable=True),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'missing_exit', 'exit_only', 'closed_by_reconciliation')",
            name=op.f("ck_stays_status_valid"),
        ),
        sa.PrimaryKeyConstraint("stay_id", name=op.f("pk_stays")),
        sa.UniqueConstraint("entry_event_id", name=op.f("uq_stays_entry_event_id")),
        sa.UniqueConstraint("exit_event_id", name=op.f("uq_stays_exit_event_id")),
    )
    op.create_index(
        "ix_stays_lot_id_student_ref_service_date",
        "stays",
        ["lot_id", "student_ref", "service_date"],
    )
    op.create_index("ix_stays_updated_at", "stays", ["updated_at"])
    op.create_index(
        "ix_stays_lot_id_open", "stays", ["lot_id"], postgresql_where=OPEN, sqlite_where=OPEN
    )

    op.create_table(
        "lot_occupancy",
        sa.Column("lot_id", sa.Text(), nullable=False),
        sa.Column("occupied", sa.Integer(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("occupied >= 0", name=op.f("ck_lot_occupancy_occupied_non_negative")),
        sa.ForeignKeyConstraint(
            ["lot_id"], ["lots.lot_id"], name=op.f("fk_lot_occupancy_lot_id_lots")
        ),
        sa.PrimaryKeyConstraint("lot_id", name=op.f("pk_lot_occupancy")),
    )
    # Every existing lot gets its counter row; the registry creates it for new lots.
    op.execute(
        "INSERT INTO lot_occupancy (lot_id, occupied, version, updated_at) "
        "SELECT lot_id, 0, 0, CURRENT_TIMESTAMP FROM lots"
    )

    if op.get_bind().dialect.name == "postgresql":
        # One statement per execute: asyncpg prepares each, and a prepared statement is single.
        op.execute(
            "CREATE FUNCTION gate_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'gate_events is append-only' "
            "USING ERRCODE = 'restrict_violation'; END $$"
        )
        op.execute(
            "CREATE TRIGGER trg_gate_events_append_only BEFORE UPDATE OR DELETE ON gate_events "
            "FOR EACH ROW EXECUTE FUNCTION gate_events_append_only()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER trg_gate_events_append_only ON gate_events")
        op.execute("DROP FUNCTION gate_events_append_only()")
    op.drop_table("lot_occupancy")
    op.drop_table("stays")
    op.drop_table("ingest_rejects")
    op.drop_table("gate_events")
    op.drop_table("ingest_batches")
    op.drop_column("gates", "last_received_at")
    op.drop_column("gates", "last_event_at")
