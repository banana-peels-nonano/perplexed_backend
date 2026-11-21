"""document.record_id: which case record a handed-out document is

Until that document is released to a team, stakeholders may only acknowledge the record
exists (aware), never explain it.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document", sa.Column("record_id", sa.Text(), sa.ForeignKey("record.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("document", "record_id")
