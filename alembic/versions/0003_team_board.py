"""team_board: the team's evidence board

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15 04:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team_board",
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_team_board_version")),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], name=op.f("fk_team_board_team_id_team")),
        sa.PrimaryKeyConstraint("team_id", name=op.f("pk_team_board")),
    )


def downgrade() -> None:
    op.drop_table("team_board")
