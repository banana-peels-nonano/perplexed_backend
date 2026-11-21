"""team access code in plain text: organisers read it out to teams (no email)

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("team", sa.Column("access_code", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("team", "access_code")
