"""team_score: organiser-given points per team per round (the leaderboard)

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_score",
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("team.id"), primary_key=True),
        sa.Column("phase_id", sa.Uuid(), sa.ForeignKey("phase.id"), primary_key=True),
        sa.Column("points", sa.Numeric(8, 2), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("breakdown", JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("team_score")
