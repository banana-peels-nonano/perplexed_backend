"""Content tables: the simulated company's internal reality.

Loaded from a case file by the seed command (step 3); read-only while an event runs.
Primary keys are the human-readable ids from the case file ('A', 'cfo', 'A-FIN-002#2'),
so a content reload can update rows in place and logs stay readable.
Runtime tables (app/models/runtime.py) use UUIDs instead.

Judge-only columns (passage.truth, passage.clue_id) are never selected by interview code.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
)
from sqlalchemy import (
    text as sql_text,  # 'text' is also a column name below
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

MODES = "('deny', 'aware', 'paraphrase', 'quote')"


class Company(Base):
    __tablename__ = "company"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # 'A', 'B'
    name: Mapped[str] = mapped_column(Text)
    topics: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=sql_text("'{}'"))
    brief: Mapped[str] = mapped_column(Text, server_default=sql_text("''"))  # its teams only


class Clue(Base):
    """Judge-only: what each clue id means. Passages point here via passage.clue_id."""

    __tablename__ = "clue"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text)


class Department(Base):
    __tablename__ = "department"

    # composite primary key: department names only need to be unique within a company
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)


class Stakeholder(Base):
    __tablename__ = "stakeholder"
    __table_args__ = (
        # a stakeholder's department must exist in the stakeholder's own company
        ForeignKeyConstraint(
            ["company_id", "department"],
            ["department.company_id", "department.name"],
            name="fk_stakeholder_department",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # 'cfo'
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"), index=True)
    department: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    persona: Mapped[str] = mapped_column(Text)
    beliefs: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=sql_text("'{}'"))
    canary: Mapped[str] = mapped_column(Text)
    # 3-5 per company, configurable; a hidden stakeholder can start inactive
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))


class Record(Base):
    __tablename__ = "record"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # 'A-FIN-002'
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    record_date: Mapped[date | None] = mapped_column(Date)


class Passage(Base):
    __tablename__ = "passage"
    __table_args__ = (CheckConstraint("truth IN ('true', 'misleading', 'false')", name="truth"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # 'A-FIN-002#2'
    record_id: Mapped[str] = mapped_column(ForeignKey("record.id"), index=True)
    # copied from the record so isolation filters need no join; the seed checks they agree
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    paraphrase: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=sql_text("'{}'"))
    fingerprints: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=sql_text("'{}'"))
    deleted: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    # judge-only
    truth: Mapped[str] = mapped_column(Text, server_default=sql_text("'true'"))
    clue_id: Mapped[str | None] = mapped_column(Text)


class StakeholderPassageAccess(Base):
    """What a stakeholder may do with a passage at each trust tier, plus unlock rules.

    No row = deny. The loader rejects rows that cross companies before they reach here.
    """

    __tablename__ = "stakeholder_passage_access"
    __table_args__ = (
        CheckConstraint(f"mode_guarded IN {MODES}", name="mode_guarded"),
        CheckConstraint(f"mode_neutral IN {MODES}", name="mode_neutral"),
        CheckConstraint(f"mode_open IN {MODES}", name="mode_open"),
    )

    stakeholder_id: Mapped[str] = mapped_column(ForeignKey("stakeholder.id"), primary_key=True)
    passage_id: Mapped[str] = mapped_column(ForeignKey("passage.id"), primary_key=True)
    mode_guarded: Mapped[str] = mapped_column(Text)
    mode_neutral: Mapped[str] = mapped_column(Text)
    mode_open: Mapped[str] = mapped_column(Text)
    # unlock rules (all present rules must pass)
    labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    requires_topic: Mapped[str | None] = mapped_column(Text)
    requires_disclosed: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=sql_text("'{}'")
    )
    phase_from: Mapped[str | None] = mapped_column(Text)
