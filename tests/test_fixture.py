"""Targeted tests for the fixture and the two pure security functions it exercises.

No database, no network, no model: these run in milliseconds and should never be skipped.
"""

from pathlib import Path

import pytest

from app.content.loader import load_case
from app.content.schema import Case, Mode
from app.interview.disclosure import check_reply
from app.interview.eligibility import eligible_passages, question_context

FIXTURE = Path(__file__).parent / "fixtures" / "northfield.toml"
CASE = load_case(FIXTURE)


def test_fixture_loads_with_expected_shape():
    a_passages = [p for p in CASE.passage if CASE.company_of_passage(p) == "A"]
    a_stakeholders = [s for s, v in CASE.stakeholder.items() if v.company == "A"]
    a_records = [r for r, v in CASE.record.items() if v.company == "A"]
    assert len(a_stakeholders) == 3
    assert len(a_records) == 8
    assert len(a_passages) == 25


def test_loader_rejects_cross_company_access():
    data = CASE.model_dump()
    data["access"].append(
        {
            "stakeholder": "cfo",
            "passage": "B-QC-001#1",
            "guarded": "quote",
            "neutral": "quote",
            "open": "quote",
        }
    )
    with pytest.raises(ValueError, match="crosses company boundary"):
        Case.model_validate(data)


def test_unlisted_passage_is_never_eligible():
    # the CFO has no access entry for the deleted chat message, at any tier or phase
    q = question_context(
        stakeholder="cfo",
        tier="open",
        label="specific",
        topics=["retests"],
        phase="round2",
        disclosed=list(CASE.passage),
    )
    assert "A-COM-001#3" not in eligible_passages(CASE, q)


@pytest.mark.parametrize("sc", CASE.scenario, ids=lambda s: s.name)
def test_eligibility_scenarios(sc):
    q = question_context(
        stakeholder=sc.stakeholder,
        tier=sc.tier,
        label=sc.label,
        topics=sc.topics,
        phase=sc.phase,
        disclosed=sc.disclosed,
    )
    got = eligible_passages(CASE, q)
    for pid, mode in sc.expect.items():
        assert pid in got, f"{pid} should be eligible"
        assert got[pid] == Mode[mode], f"{pid}: expected {mode}, got {got[pid].name}"
    for pid in sc.absent:
        assert pid not in got, f"{pid} must not be eligible"
    own_company = CASE.stakeholder[sc.stakeholder].company
    assert all(CASE.company_of_passage(p) == own_company for p in got)


@pytest.mark.parametrize("cc", CASE.citation_case, ids=lambda c: c.name)
def test_citation_cases(cc):
    provided = {pid: Mode[m] for pid, m in cc.provided.items()}
    result = check_reply(CASE, cc.stakeholder, provided, cc.cited, cc.reply)
    assert result.ok is cc.expect_ok, result.details
    if not cc.expect_ok:
        assert set(cc.expect_violations) <= set(result.violations), result.details
    else:
        assert [c.passage_id for c in result.cards] == cc.expect_cards
        for card in result.cards:  # cards carry authentic text, not the model's wording
            assert card.excerpt == CASE.passage[card.passage_id].text
