"""rounds: documents handed to teams, and each team's status per round

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19 01:00:00

Rounds themselves are phase rows (organisers can now add, rename, time and reorder them);
no schema change was needed for that.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("phase_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=True),
        sa.Column("team_ids", postgresql.ARRAY(sa.UUID()), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('markdown', 'file')", name=op.f("ck_document_kind")),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_document_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["event.id"], name=op.f("fk_document_event_id_event")
        ),
        sa.ForeignKeyConstraint(
            ["phase_id"], ["phase.id"], name=op.f("fk_document_phase_id_phase")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
    )
    op.create_table(
        "team_round_status",
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("phase_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('advancing', 'eliminated', 'waitlisted', 'pending')",
            name=op.f("ck_team_round_status_status"),
        ),
        sa.ForeignKeyConstraint(
            ["phase_id"], ["phase.id"], name=op.f("fk_team_round_status_phase_id_phase")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["team.id"], name=op.f("fk_team_round_status_team_id_team")
        ),
        sa.PrimaryKeyConstraint("team_id", "phase_id", name=op.f("pk_team_round_status")),
    )


def downgrade() -> None:
    op.drop_table("team_round_status")
    op.drop_table("document")
