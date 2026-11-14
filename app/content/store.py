"""Content held in memory: loaded from the database once, then read on every question.

Content is read-only while an event runs, so re-reading 30+ access rules from Postgres on
every question would be wasted work. The database stays the single source of truth: this
is a cache of it, rebuilt into the same validated `Case` the fixture tests use, so the
eligibility, ranking and prompt code run identically in tests and in production.
`invalidate()` forces a reload (the future 'reload content' admin action calls it).

Judge-only fields (clue ids, contradictions) are deliberately NOT loaded: nothing on the
interview path can leak what isn't in memory.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.schema import Case
from app.models import (
    Company,
    Department,
    Event,
    Passage,
    Phase,
    Record,
    Stakeholder,
    StakeholderPassageAccess,
)

DEFAULT_LABELS = [
    "specific",
    "broad",
    "vague",
    "repetitive",
    "hostile",
    "prompt_attack",
    "off_topic",
]


async def current_event(session: AsyncSession) -> Event:
    event = await session.scalar(select(Event).order_by(Event.created_at).limit(1))
    if event is None:
        raise RuntimeError("no event in the database: run `python -m app.seed <case.toml>` first")
    return event


async def load_case_from_db(session: AsyncSession) -> Case:
    event = await current_event(session)
    departments: dict[str, list[str]] = {}
    for company_id, name in await session.execute(select(Department.company_id, Department.name)):
        departments.setdefault(company_id, []).append(name)

    data = {
        "schema_version": 1,
        "case_id": event.case_id,
        "phases": list(
            await session.scalars(
                select(Phase.key).where(Phase.event_id == event.id).order_by(Phase.seq)
            )
        ),
        "labels": event.config.get("labels", DEFAULT_LABELS),
        "company": {
            c.id: {"name": c.name, "departments": departments.get(c.id, []), "topics": c.topics}
            for c in await session.scalars(select(Company))
        },
        "stakeholder": {
            s.id: {
                "company": s.company_id,
                "department": s.department,
                "name": s.name,
                "role": s.role,
                "canary": s.canary,
                "persona": s.persona,
                "beliefs": s.beliefs,
            }
            for s in await session.scalars(
                select(Stakeholder)
                .where(Stakeholder.is_active.is_(True))
                .order_by(Stakeholder.sort_order)
            )
        },
        "record": {
            r.id: {
                "company": r.company_id,
                "kind": r.kind,
                "title": r.title,
                "date": r.record_date.isoformat() if r.record_date else "",
            }
            for r in await session.scalars(select(Record))
        },
        "passage": {
            p.id: {
                "record": p.record_id,
                "topics": p.topics,
                "text": p.text,
                "fingerprints": p.fingerprints,
                "deleted": p.deleted,
            }  # clue_id and truth intentionally left out
            for p in await session.scalars(select(Passage))
        },
        "access": [],
    }
    active = set(data["stakeholder"])
    for a in await session.scalars(select(StakeholderPassageAccess)):
        if a.stakeholder_id not in active:
            continue
        data["access"].append(
            {
                "stakeholder": a.stakeholder_id,
                "passage": a.passage_id,
                "guarded": a.mode_guarded,
                "neutral": a.mode_neutral,
                "open": a.mode_open,
                "labels": a.labels,
                "requires_topic": a.requires_topic,
                "requires_disclosed": a.requires_disclosed,
                "phase_from": a.phase_from,
            }
        )
    return Case.model_validate(data)  # same validation as the file loader


class ContentStore:
    def __init__(self) -> None:
        self._case: Case | None = None
        self._lock = asyncio.Lock()

    async def get(self, maker: async_sessionmaker[AsyncSession]) -> Case:
        if self._case is None:
            async with self._lock:  # many first requests at once -> one load
                if self._case is None:
                    async with maker() as session:
                        self._case = await load_case_from_db(session)
        return self._case

    def invalidate(self) -> None:
        self._case = None
