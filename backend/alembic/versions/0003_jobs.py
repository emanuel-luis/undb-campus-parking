"""Jobs: pipeline_runs (run log and queue) and service_days.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ACTIVE = sa.text("status IN ('queued', 'running')")
QUEUED = sa.text("status = 'queued'")


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_read", sa.Integer(), nullable=True),
        sa.Column("rows_written", sa.Integer(), nullable=True),
        sa.Column("rows_rejected", sa.Integer(), nullable=True),
        sa.Column(
            "metrics",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "job IN ('daily', 'close_day', 'warehouse')", name=op.f("ck_pipeline_runs_job_valid")
        ),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'operator', 'cli')",
            name=op.f("ck_pipeline_runs_trigger_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_pipeline_runs_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["pipeline_runs.run_id"],
            name=op.f("fk_pipeline_runs_parent_run_id_pipeline_runs"),
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_pipeline_runs")),
    )
    op.create_index(
        "uq_pipeline_runs_job_service_date_active",
        "pipeline_runs",
        ["job", "service_date"],
        unique=True,
        postgresql_where=ACTIVE,
        sqlite_where=ACTIVE,
    )
    op.create_index(
        "ix_pipeline_runs_requested_at_queued",
        "pipeline_runs",
        ["requested_at"],
        postgresql_where=QUEUED,
        sqlite_where=QUEUED,
    )
    op.create_table(
        "service_days",
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("exported_events", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["pipeline_runs.run_id"], name=op.f("fk_service_days_run_id_pipeline_runs")
        ),
        sa.PrimaryKeyConstraint("service_date", name=op.f("pk_service_days")),
    )


def downgrade() -> None:
    op.drop_table("service_days")
    op.drop_table("pipeline_runs")
