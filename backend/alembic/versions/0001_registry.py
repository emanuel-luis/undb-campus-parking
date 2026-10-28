"""Registry: lots, lot_capacity_history, gates, students.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lots",
        sa.Column("lot_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("lot_id", name=op.f("pk_lots")),
    )
    op.create_table(
        "lot_capacity_history",
        sa.Column("lot_id", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.CheckConstraint("capacity > 0", name=op.f("ck_lot_capacity_history_capacity_positive")),
        sa.ForeignKeyConstraint(
            ["lot_id"], ["lots.lot_id"], name=op.f("fk_lot_capacity_history_lot_id_lots")
        ),
        sa.PrimaryKeyConstraint("lot_id", "valid_from", name=op.f("pk_lot_capacity_history")),
    )
    op.create_table(
        "gates",
        sa.Column("gate_id", sa.Text(), nullable=False),
        sa.Column("lot_id", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("direction IN ('entry', 'exit')", name=op.f("ck_gates_direction_valid")),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.lot_id"], name=op.f("fk_gates_lot_id_lots")),
        sa.PrimaryKeyConstraint("gate_id", name=op.f("pk_gates")),
    )
    op.create_table(
        "students",
        sa.Column("student_ref", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("student_ref", name=op.f("pk_students")),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            op.f("ck_students_student_ref_format"),
            "students",
            "student_ref ~ '^stu_[0-9a-f]{12}$'",
        )


def downgrade() -> None:
    op.drop_table("students")
    op.drop_table("gates")
    op.drop_table("lot_capacity_history")
    op.drop_table("lots")
