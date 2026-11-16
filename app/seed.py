"""Load a case file into the database, create the event, and create test teams.

    python -m app.seed tests/fixtures/northfield.toml --teams-per-company 2

Safe to run again (idempotent):
  - content rows are upserted: inserted if new, updated if the id already exists;
  - access rules are REPLACED for the case's stakeholders, so a rule removed from the file
    stops applying (an access rule that lingered after removal would be a leak);
  - the event and its phases are created once per case_id;
  - teams are created only if their code doesn't exist yet. Existing teams keep their
    access codes, which are never stored in plain text, so they can't be shown again;
    use --rotate-codes to issue new ones.
Content rows are never deleted here: messages and disclosures may already point at them.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.codes import generate_access_code, hash_access_code
from app.content.loader import load_case
from app.content.schema import Case
from app.db import get_engine, make_sessionmaker
from app.interview.trust import DEFAULT_HR, DEFAULT_TRUST_RULES
from app.models import (
    Clue,
    Company,
    Department,
    Event,
    Passage,
    Phase,
    Record,
    Stakeholder,
    StakeholderPassageAccess,
    Team,
    TeamStakeholderState,
)

# Organiser-editable defaults, stored in event.config (not hard-coded in services).
DEFAULT_EVENT_CONFIG = {
    "default_quota": 10,
    "trust_start": 50,
    "tier_thresholds": {"neutral": 35, "open": 70},  # trust >= value -> that tier
    "evidence_token_budget": 1200,
    "max_passages": 4,
    "max_sessions_per_team": 2,
    "models": {"answer": "claude-haiku-5-5", "classify": "claude-haiku-5-5"},
    # placeholders until the seniors confirm them; see app/interview/trust.py
    "trust_rules": DEFAULT_TRUST_RULES,
    "hr": DEFAULT_HR,
}

# Rulebook defaults; organisers change them per event. 0 = no timer.
DEFAULT_PHASE_SECONDS = {
    "briefing": 900,
    "solo": 1800,
    "alliance_window": 900,
    "joint": 1800,
    "submission_closed": 0,
    "round2": 0,
}
INTERVIEW_PHASES = {"solo", "alliance_window", "joint"}


@dataclass
class SeedReport:
    counts: dict[str, int] = field(default_factory=dict)
    new_codes: dict[str, str] = field(default_factory=dict)  # team code -> access code
    existing_teams: list[str] = field(default_factory=list)


async def _upsert(session: AsyncSession, model, rows: list[dict], keys: list[str]) -> None:
    """INSERT ... ON CONFLICT (keys) DO UPDATE SET every other column."""
    if not rows:
        return
    stmt = insert(model).values(rows)
    update_cols = {c: stmt.excluded[c] for c in rows[0] if c not in keys}
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=keys, set_=update_cols)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=keys)
    await session.execute(stmt)


async def seed_case(session: AsyncSession, case: Case) -> None:
    """Upsert companies, departments, stakeholders, records, passages; replace access rules."""
    await _upsert(
        session,
        Company,
        [
            {"id": cid, "name": c.name, "topics": c.topics, "brief": c.brief}
            for cid, c in case.company.items()
        ],
        ["id"],
    )
    # judge-only: what each clue means (passages carry clue ids)
    await _upsert(
        session,
        Clue,
        [{"id": k, "description": v} for k, v in case.clue.items()],
        ["id"],
    )
    await _upsert(
        session,
        Department,
        [{"company_id": cid, "name": d} for cid, c in case.company.items() for d in c.departments],
        ["company_id", "name"],
    )
    await _upsert(
        session,
        Stakeholder,
        [
            {
                "id": sid,
                "company_id": s.company,
                "department": s.department,
                "name": s.name,
                "role": s.role,
                "persona": s.persona,
                "beliefs": s.beliefs,
                "canary": s.canary,
                "sort_order": i,
            }
            for i, (sid, s) in enumerate(case.stakeholder.items())
        ],
        ["id"],
    )
    await _upsert(
        session,
        Record,
        [
            {
                "id": rid,
                "company_id": r.company,
                "kind": r.kind,
                "title": r.title,
                "record_date": date.fromisoformat(r.date),
            }
            for rid, r in case.record.items()
        ],
        ["id"],
    )
    await _upsert(
        session,
        Passage,
        [
            {
                "id": pid,
                "record_id": p.record,
                "company_id": case.company_of_passage(pid),  # copied from the record
                "text": p.text,
                "paraphrase": p.paraphrase,
                "topics": p.topics,
                "fingerprints": p.fingerprints,
                "deleted": p.deleted,
                "truth": p.truth,
                "clue_id": p.clue,
            }
            for pid, p in case.passage.items()
        ],
        ["id"],
    )
    # Replace, don't merge: removing a rule from the file must remove it here.
    await session.execute(
        delete(StakeholderPassageAccess).where(
            StakeholderPassageAccess.stakeholder_id.in_(list(case.stakeholder))
        )
    )
    await _upsert(
        session,
        StakeholderPassageAccess,
        [
            {
                "stakeholder_id": a.stakeholder,
                "passage_id": a.passage,
                "mode_guarded": a.guarded,
                "mode_neutral": a.neutral,
                "mode_open": a.open,
                "labels": a.labels,
                "requires_topic": a.requires_topic,
                "requires_disclosed": a.requires_disclosed,
                "phase_from": a.phase_from,
            }
            for a in case.access
        ],
        ["stakeholder_id", "passage_id"],
    )


async def ensure_event(session: AsyncSession, case: Case, name: str) -> Event:
    """Create the event and its phases once per case_id; return the existing one otherwise."""
    event = await session.scalar(select(Event).where(Event.case_id == case.case_id))
    if event is not None:
        event.brief = case.brief  # the brief follows the file, like the rest of the content
        return event
    config = {**DEFAULT_EVENT_CONFIG, "labels": list(case.labels)}
    event = Event(name=name, case_id=case.case_id, config=config, brief=case.brief)
    session.add(event)
    await session.flush()  # sends the INSERT so event.id is filled in
    for seq, key in enumerate(case.phases):
        session.add(
            Phase(
                event_id=event.id,
                key=key,
                display_name=key.replace("_", " ").title(),
                seq=seq,
                planned_seconds=DEFAULT_PHASE_SECONDS.get(key, 0),
                capabilities={"interview": key in INTERVIEW_PHASES},
            )
        )
    return event


async def ensure_teams(
    session: AsyncSession,
    event: Event,
    companies: list[str],
    per_company: int,
    rotate_codes: bool = False,
) -> tuple[dict[str, str], list[str]]:
    """Create teams A01..An, B01..Bn (if missing) and their per-stakeholder state rows."""
    config = event.config
    new_codes: dict[str, str] = {}
    existing: list[str] = []
    for company_id in companies:
        for n in range(1, per_company + 1):
            code = f"{company_id}{n:02d}"
            team = await session.scalar(select(Team).where(Team.code == code))
            if team is None:
                access_code = generate_access_code()
                team = Team(
                    code=code,
                    name=f"Test team {code}",
                    company_id=company_id,
                    access_code_hash=hash_access_code(access_code),
                    max_sessions=config["max_sessions_per_team"],
                )
                session.add(team)
                await session.flush()
                new_codes[code] = access_code
            elif rotate_codes:
                access_code = generate_access_code()
                team.access_code_hash = hash_access_code(access_code)
                new_codes[code] = access_code
            else:
                existing.append(code)
            await ensure_team_state(session, team, config)
    return new_codes, existing


async def ensure_team_state(session: AsyncSession, team: Team, config: dict) -> None:
    """One state row per active stakeholder of the team's company; existing rows untouched."""
    stakeholder_ids = (
        await session.scalars(
            select(Stakeholder.id).where(
                Stakeholder.company_id == team.company_id, Stakeholder.is_active.is_(True)
            )
        )
    ).all()
    if not stakeholder_ids:
        return
    stmt = insert(TeamStakeholderState).values(
        [
            {
                "team_id": team.id,
                "stakeholder_id": sid,
                "trust": config["trust_start"],
                "quota": config["default_quota"],
            }
            for sid in stakeholder_ids
        ]
    )
    await session.execute(stmt.on_conflict_do_nothing(index_elements=["team_id", "stakeholder_id"]))


async def start_phase(session: AsyncSession, event: Event, key: str) -> None:
    """Dev/test shortcut until the organiser phase controls exist (M2): make `key` the
    running phase. Any other running or paused phase goes back to 'scheduled'."""
    keys = (await session.scalars(select(Phase.key).where(Phase.event_id == event.id))).all()
    if key not in keys:
        raise SystemExit(f"unknown phase {key!r}; choose from {list(keys)}")
    await session.execute(
        update(Phase)
        .where(Phase.event_id == event.id, Phase.status.in_(["running", "paused"]))
        .values(status="scheduled", started_at=None)
    )
    await session.execute(
        update(Phase)
        .where(Phase.event_id == event.id, Phase.key == key)
        .values(status="running", started_at=func.now())
    )


async def count_rows(session: AsyncSession) -> dict[str, int]:
    models = [
        Company,
        Stakeholder,
        Record,
        Passage,
        StakeholderPassageAccess,
        Event,
        Phase,
        Team,
        TeamStakeholderState,
    ]
    return {
        m.__tablename__: await session.scalar(select(func.count()).select_from(m)) for m in models
    }


async def run_seed(
    session: AsyncSession,
    case_path: str | Path,
    teams_per_company: int = 0,
    event_name: str = "PERPLEXED dev",
    rotate_codes: bool = False,
    start_phase_key: str | None = None,
) -> SeedReport:
    case = load_case(case_path)
    report = SeedReport()
    await seed_case(session, case)
    event = await ensure_event(session, case, event_name)
    if teams_per_company:
        report.new_codes, report.existing_teams = await ensure_teams(
            session, event, list(case.company), teams_per_company, rotate_codes
        )
    if start_phase_key:
        await start_phase(session, event, start_phase_key)
    report.counts = await count_rows(session)
    return report


async def _main(args: argparse.Namespace) -> None:
    engine = get_engine()
    try:
        async with make_sessionmaker(engine)() as session, session.begin():
            # session.begin(): everything above commits together, or nothing does
            report = await run_seed(
                session,
                args.case,
                args.teams_per_company,
                args.event_name,
                args.rotate_codes,
                args.start_phase,
            )
    finally:
        await engine.dispose()
    print("rows now in the database:")
    for table, n in report.counts.items():
        print(f"  {table:28s} {n}")
    if report.new_codes:
        print("\nACCESS CODES (shown once; only their hashes are stored):")
        for team, code in report.new_codes.items():
            print(f"  {team}  {code}")
    if args.start_phase:
        print(f"\nphase {args.start_phase!r} is now running")
    if report.existing_teams:
        print(
            f"\nexisting teams kept their codes: {', '.join(report.existing_teams)}"
            "  (use --rotate-codes to issue new ones)"
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Seed a case into the database.")
    p.add_argument("case", help="path to a case .toml file")
    p.add_argument("--teams-per-company", type=int, default=0)
    p.add_argument("--event-name", default="PERPLEXED dev")
    p.add_argument("--rotate-codes", action="store_true", help="issue new codes to existing teams")
    p.add_argument("--start-phase", help="dev only: make this phase running, e.g. solo")
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
