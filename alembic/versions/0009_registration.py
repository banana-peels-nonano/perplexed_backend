"""team registration: members (thapar.edu addresses) and a log of emails sent

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19 03:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_log",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=True),
        sa.Column("recipients", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('sent', 'failed', 'not_configured')", name=op.f("ck_email_log_status")
        ),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], name=op.f("fk_email_log_team_id_team")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_log")),
    )
    op.create_index(op.f("ix_email_log_team_id"), "email_log", ["team_id"], unique=False)
    op.create_table(
        "team_member",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], name=op.f("fk_team_member_team_id_team")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_member")),
        sa.UniqueConstraint("email", name=op.f("uq_team_member_email")),
    )
    op.create_index(op.f("ix_team_member_team_id"), "team_member", ["team_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_team_member_team_id"), table_name="team_member")
    op.drop_table("team_member")
    op.drop_index(op.f("ix_email_log_team_id"), table_name="email_log")
    op.drop_table("email_log")
