"""team_stakeholder_state.strikes and locked_until: HR lockouts

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15 18:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "team_stakeholder_state",
        sa.Column("strikes", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "team_stakeholder_state",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("team_stakeholder_state", "locked_until")
    op.drop_column("team_stakeholder_state", "strikes")
