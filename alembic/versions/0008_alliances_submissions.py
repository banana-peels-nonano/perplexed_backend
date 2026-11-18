"""alliances, submissions (every version kept), and per-stakeholder HR counting

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19 02:30:00

team_department_state.reset_at: when a department's strike count last started from zero,
so hr.strike_scope = "stakeholder" can count one person's strikes since then.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alliance",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("by_organisers", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dissolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'locked', 'dissolved')", name=op.f("ck_alliance_status")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["event.id"], name=op.f("fk_alliance_event_id_event")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["team.id"], name=op.f("fk_alliance_requested_by_team")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alliance")),
    )
    op.create_table(
        "submission_version",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("phase_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("final", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["phase_id"], ["phase.id"], name=op.f("fk_submission_version_phase_id_phase")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["team.id"], name=op.f("fk_submission_version_team_id_team")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_version")),
        sa.UniqueConstraint("team_id", "phase_id", "version", name="uq_submission"),
    )
    op.create_index(
        op.f("ix_submission_version_team_id"), "submission_version", ["team_id"], unique=False
    )
    op.create_table(
        "alliance_member",
        sa.Column("alliance_id", sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(
            ["alliance_id"], ["alliance.id"], name=op.f("fk_alliance_member_alliance_id_alliance")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["team.id"], name=op.f("fk_alliance_member_team_id_team")
        ),
        sa.PrimaryKeyConstraint("alliance_id", "team_id", name=op.f("pk_alliance_member")),
    )
    op.create_index(
        "uq_alliance_member_one_locked",
        "alliance_member",
        ["team_id"],
        unique=True,
        postgresql_where=sa.text("locked"),
    )
    op.add_column(
        "team_department_state", sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("team_department_state", "reset_at")
    op.drop_index(
        "uq_alliance_member_one_locked",
        table_name="alliance_member",
        postgresql_where=sa.text("locked"),
    )
    op.drop_table("alliance_member")
    op.drop_index(op.f("ix_submission_version_team_id"), table_name="submission_version")
    op.drop_table("submission_version")
    op.drop_table("alliance")
