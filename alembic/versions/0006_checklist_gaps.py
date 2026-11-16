"""checklist gaps: HR per department, flag history, time add-back, announcements, briefs, clues

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-16 22:00:00

HR strikes and lockouts move from team_stakeholder_state to team_department_state.
Existing strike counts are dropped, not copied: they were per stakeholder and only ever
held test data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clue",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clue")),
    )
    op.create_table(
        "announcement",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_announcement_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["event.id"], name=op.f("fk_announcement_event_id_event")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_announcement")),
    )
    op.create_table(
        "team_department_state",
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("strikes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("strikes >= 0", name=op.f("ck_team_department_state_strikes")),
        sa.ForeignKeyConstraint(
            ["company_id", "department"],
            ["department.company_id", "department.name"],
            name="fk_team_department_state_department",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["team.id"], name=op.f("fk_team_department_state_team_id_team")
        ),
        sa.PrimaryKeyConstraint(
            "team_id", "company_id", "department", name=op.f("pk_team_department_state")
        ),
    )
    op.create_table(
        "team_time_bonus",
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("phase_id", sa.UUID(), nullable=False),
        sa.Column("seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("seconds >= 0", name=op.f("ck_team_time_bonus_seconds")),
        sa.ForeignKeyConstraint(
            ["phase_id"], ["phase.id"], name=op.f("fk_team_time_bonus_phase_id_phase")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["team.id"], name=op.f("fk_team_time_bonus_team_id_team")
        ),
        sa.PrimaryKeyConstraint("team_id", "phase_id", name=op.f("pk_team_time_bonus")),
    )
    op.create_table(
        "hr_flag",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("stakeholder_id", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('strike', 'warning', 'lockout')", name=op.f("ck_hr_flag_outcome")
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["message.id"], name=op.f("fk_hr_flag_message_id_message")
        ),
        sa.ForeignKeyConstraint(
            ["stakeholder_id"],
            ["stakeholder.id"],
            name=op.f("fk_hr_flag_stakeholder_id_stakeholder"),
        ),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], name=op.f("fk_hr_flag_team_id_team")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_flag")),
    )
    op.create_index(op.f("ix_hr_flag_team_id"), "hr_flag", ["team_id"], unique=False)
    op.add_column(
        "company", sa.Column("brief", sa.Text(), server_default=sa.text("''"), nullable=False)
    )
    op.add_column(
        "event", sa.Column("brief", sa.Text(), server_default=sa.text("''"), nullable=False)
    )
    op.drop_column("team_stakeholder_state", "strikes")
    op.drop_column("team_stakeholder_state", "locked_until")


def downgrade() -> None:
    op.add_column(
        "team_stakeholder_state",
        sa.Column(
            "locked_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
    )
    op.add_column(
        "team_stakeholder_state",
        sa.Column(
            "strikes",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.drop_column("event", "brief")
    op.drop_column("company", "brief")
    op.drop_index(op.f("ix_hr_flag_team_id"), table_name="hr_flag")
    op.drop_table("hr_flag")
    op.drop_table("team_time_bonus")
    op.drop_table("team_department_state")
    op.drop_table("announcement")
    op.drop_table("clue")
