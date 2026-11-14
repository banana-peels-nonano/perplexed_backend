"""Step 3: the seed loads the fixture faithfully, is safe to re-run, and creates teams."""

from pathlib import Path

from sqlalchemy import select

from app.auth.codes import hash_access_code, verify_access_code
from app.content.loader import load_case
from app.db import make_sessionmaker
from app.models import Passage, Phase, StakeholderPassageAccess, Team, TeamStakeholderState
from app.seed import run_seed

FIXTURE = Path(__file__).parent / "fixtures" / "northfield.toml"
CASE = load_case(FIXTURE)


async def _seed(engine, **kwargs):
    async with make_sessionmaker(engine)() as session, session.begin():
        return await run_seed(session, FIXTURE, **kwargs)


async def test_seed_loads_the_fixture(db):
    report = await _seed(db)
    assert report.counts["company"] == 2
    assert report.counts["stakeholder"] == 4
    assert report.counts["record"] == 9
    assert report.counts["passage"] == 27
    assert report.counts["stakeholder_passage_access"] == len(CASE.access)
    assert report.counts["event"] == 1
    assert report.counts["phase"] == len(CASE.phases)
    # passage.company_id was copied from each passage's record
    async with make_sessionmaker(db)() as s:
        rows = (await s.execute(select(Passage.id, Passage.company_id))).all()
    assert all(company == CASE.company_of_passage(pid) for pid, company in rows)


async def test_seed_twice_changes_nothing(db):
    first = await _seed(db, teams_per_company=1)
    second = await _seed(db, teams_per_company=1)
    assert first.counts == second.counts
    assert second.new_codes == {}  # existing teams keep their codes
    assert sorted(second.existing_teams) == ["A01", "B01"]


async def test_removed_access_rule_stops_applying(db, tmp_path):
    await _seed(db)
    # same case, minus the rule that lets the CFO quote the 15 percent figure
    text = FIXTURE.read_text()
    anchor = 'stakeholder = "cfo"\npassage = "A-FIN-002#2"\n'
    assert anchor in text
    start = text.rindex("[[access]]", 0, text.index(anchor))  # the entry's own header
    end = text.index("[[access]]", start + 1)  # the next entry
    trimmed = tmp_path / "trimmed.toml"
    trimmed.write_text(text[:start] + text[end:])
    async with make_sessionmaker(db)() as session, session.begin():
        await run_seed(session, trimmed)
        gone = await session.scalar(
            select(StakeholderPassageAccess).where(
                StakeholderPassageAccess.stakeholder_id == "cfo",
                StakeholderPassageAccess.passage_id == "A-FIN-002#2",
            )
        )
    assert gone is None


async def test_teams_get_hashed_codes_and_state_rows(db):
    report = await _seed(db, teams_per_company=2)
    assert sorted(report.new_codes) == ["A01", "A02", "B01", "B02"]
    async with make_sessionmaker(db)() as s:
        team = await s.scalar(select(Team).where(Team.code == "A01"))
        code = report.new_codes["A01"]
        assert team.access_code_hash != code  # never stored in plain text
        assert verify_access_code(code, team.access_code_hash)
        assert verify_access_code(code.lower().replace("-", " "), team.access_code_hash)
        assert not verify_access_code("AAAA-BBBB-CCCC", team.access_code_hash)
        states = (
            await s.scalars(
                select(TeamStakeholderState).where(TeamStakeholderState.team_id == team.id)
            )
        ).all()
    # one row per Northfield stakeholder, starting at the configured quota and trust
    assert sorted(st.stakeholder_id for st in states) == ["cfo", "ops_head", "qa_lead"]
    assert all(st.quota == 10 and st.trust == 50 and st.questions_used == 0 for st in states)


async def test_rotate_codes_invalidates_the_old_code(db):
    first = await _seed(db, teams_per_company=1)
    second = await _seed(db, teams_per_company=1, rotate_codes=True)
    async with make_sessionmaker(db)() as s:
        team = await s.scalar(select(Team).where(Team.code == "A01"))
        phases = (await s.scalars(select(Phase.key).order_by(Phase.seq))).all()
    assert not verify_access_code(first.new_codes["A01"], team.access_code_hash)
    assert team.access_code_hash == hash_access_code(second.new_codes["A01"])
    assert phases == CASE.phases
