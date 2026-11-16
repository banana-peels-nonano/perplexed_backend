"""Step 10: questions move hidden trust; hostile questioning ends in an HR walk-out.
Step 13: HR is per team per department, and every strike is logged."""

import uuid

import pytest
from sqlalchemy import select, text

from app.db import make_sessionmaker
from app.interview.trust import judge
from app.llm.base import ProviderError
from app.models import HrFlag, Message, Team, TeamDepartmentState, TeamStakeholderState

API = "/api/v1"
POLITE = {"label": "specific", "topics": ["qa_budget"], "respectful": True}
HOSTILE = {"label": "hostile", "topics": ["qa_budget"], "respectful": False}
REPLY = {"reply": "We trimmed QA spend to protect our margin.", "cited": []}


# ------------------------------------------------------------------ the rules
@pytest.mark.parametrize(
    "label, respectful, strikes, delta, strike, warn, lockout",
    [
        ("specific", True, 0, 4, False, False, False),
        ("broad", True, 0, 2, False, False, False),
        ("broad", False, 0, -3, False, False, False),  # rude tone alone costs, no strike
        ("repetitive", True, 0, -2, False, False, False),
        ("hostile", True, 0, -10, True, False, False),
        ("hostile", False, 0, -15, True, False, False),  # -10 -5, at the -15 cap
        ("prompt_attack", True, 1, -10, True, True, False),  # second strike: a warning
        ("hostile", True, 2, -10, True, False, True),  # third strike: the walk-out
        ("specific", True, 2, 4, False, False, False),  # good questions don't add strikes
    ],
)
def test_judge(label, respectful, strikes, delta, strike, warn, lockout):
    v = judge(label, respectful, strikes, config={})
    assert (v.delta, v.strike, v.warn, v.lockout) == (delta, strike, warn, lockout)


def test_event_config_overrides_any_part_of_the_rules():
    config = {"trust_rules": {"deltas": {"specific": 6}}, "hr": {"strikes_to_lockout": 2}}
    assert judge("specific", True, 0, config).delta == 6
    assert judge("broad", True, 0, config).delta == 2  # untouched defaults stay
    assert judge("hostile", True, 1, config).lockout


# ------------------------------------------------------------------ through the API
async def _team(make_client, seeded, code="A01"):
    client = make_client()
    r = await client.post(f"{API}/auth/login", json={"access_code": seeded[code]})
    assert r.status_code == 200
    return client


async def _ask(client, question="What happened to QA spend?", request_id=None, who="cfo"):
    return await client.post(
        f"{API}/stakeholders/{who}/questions",
        json={"client_request_id": str(request_id or uuid.uuid4()), "text": question},
    )


async def _state(db, who="cfo"):
    async with make_sessionmaker(db)() as s:
        return await s.scalar(
            select(TeamStakeholderState)
            .join(Team, Team.id == TeamStakeholderState.team_id)
            .where(TeamStakeholderState.stakeholder_id == who, Team.code == "A01")
        )


async def _dept(db, department="finance"):
    async with make_sessionmaker(db)() as s:
        return await s.scalar(
            select(TeamDepartmentState)
            .join(Team, Team.id == TeamDepartmentState.team_id)
            .where(TeamDepartmentState.department == department, Team.code == "A01")
        )


async def _flags(db):
    async with make_sessionmaker(db)() as s:
        return [
            (f.stakeholder_id, f.department, f.outcome)
            for f in await s.scalars(select(HrFlag).order_by(HrFlag.created_at))
        ]


async def _sql(db, statement):
    async with db.begin() as conn:
        await conn.execute(text(statement))


async def test_polite_specific_questions_build_trust(seeded, make_client, fake, db):
    fake.queues["classify"] += [POLITE, POLITE]
    client = await _team(make_client, seeded)
    for _ in range(2):
        assert (await _ask(client)).status_code == 200
    assert (await _state(db)).trust == 58  # 50 + 4 + 4


async def test_a_hostile_question_changes_the_answer_it_gets(seeded, make_client, fake, db):
    await _sql(db, "UPDATE team_stakeholder_state SET trust = 72")  # open
    fake.queues["classify"].append(HOSTILE)
    client = await _team(make_client, seeded)
    assert (await _ask(client, "Stop lying to us about the budget!")).status_code == 200
    # 72 - 15 = 57: neutral, and the answer to THIS question is already the neutral one
    [call] = fake.calls_for("answer")
    assert "neutral:" in call.prompt.final_user and "open:" not in call.prompt.final_user
    assert (await _state(db)).trust == 57


async def test_hostile_questioning_warns_then_walks_out(seeded, make_client, fake, db):
    fake.queues["classify"] += [HOSTILE, HOSTILE, HOSTILE]
    fake.queues["answer"] += [REPLY, REPLY]
    client = await _team(make_client, seeded)

    await _ask(client, "You people are useless.")
    second = await _ask(client, "Answer me, you liar.")
    assert second.json()["kind"] == "answer"
    # one strike short of HR: she is told to say so, in character
    assert "one more question like that" in fake.calls_for("answer")[1].prompt.final_user

    third = (await _ask(client, "Pathetic. Just admit it.")).json()
    assert third["kind"] == "hr_notice" and "files a complaint with HR" in third["answer"]
    assert len(fake.calls_for("answer")) == 2  # no answer was generated for the third
    assert third["questions_remaining"] == 7  # the question still counts

    assert "Nobody in finance" in third["answer"] and "10 minutes" in third["answer"]

    blocked = await _ask(client, "Sorry. Can we talk?")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "HR_LOCKOUT"
    details = blocked.json()["error"]["details"]
    assert 0 < details["retry_after_seconds"] <= 601 and details["department"] == "finance"
    listed = {s["id"]: s for s in (await client.get(f"{API}/stakeholders")).json()}
    assert 0 < listed["cfo"]["available_in_seconds"] <= 601
    assert listed["ops_head"]["available_in_seconds"] == 0  # operations is a different dept
    assert listed["cfo"]["questions_remaining"] == 7  # the refused ask wasn't charged

    thread = (await client.get(f"{API}/stakeholders/cfo/messages")).json()
    assert thread[-1]["role"] == "notice"
    assert (await _state(db)).trust == 5  # 50 - 15 - 15 - 15
    assert (await _dept(db)).strikes == 3
    assert await _flags(db) == [
        ("cfo", "finance", "strike"),
        ("cfo", "finance", "warning"),
        ("cfo", "finance", "lockout"),
    ]


async def test_hr_counts_and_locks_the_whole_department(seeded, make_client, fake, db):
    # ops_head and qa_lead are both in operations: strikes against either add up, and one
    # complaint closes both doors
    fake.queues["classify"] += [HOSTILE, HOSTILE, HOSTILE]
    fake.queues["answer"] += [REPLY, REPLY]
    client = await _team(make_client, seeded)
    assert (await _ask(client, "Useless.", who="ops_head")).status_code == 200
    second = await _ask(client, "Liar.", who="qa_lead")
    assert second.json()["kind"] == "answer"
    assert "one more question like that" in fake.calls_for("answer")[1].prompt.final_user
    third = (await _ask(client, "Pathetic.", who="ops_head")).json()
    assert third["kind"] == "hr_notice"

    refused = await _ask(client, "Can we talk?", who="qa_lead")
    assert refused.json()["error"]["code"] == "HR_LOCKOUT"
    listed = {s["id"]: s for s in (await client.get(f"{API}/stakeholders")).json()}
    assert listed["qa_lead"]["available_in_seconds"] > 0
    assert listed["ops_head"]["available_in_seconds"] > 0
    assert listed["cfo"]["available_in_seconds"] == 0
    # trust is still per person: qa_lead took one hostile question, ops_head two
    assert (await _state(db, "qa_lead")).trust == 35 and (await _state(db, "ops_head")).trust == 20


async def test_after_the_lockout_she_talks_again_but_remembers(seeded, make_client, fake, db):
    fake.queues["classify"] += [HOSTILE, HOSTILE, HOSTILE, POLITE]
    fake.queues["answer"] += [REPLY, REPLY, REPLY]
    client = await _team(make_client, seeded)
    for q in ("Useless.", "Liar.", "Pathetic."):
        await _ask(client, q)
    await _sql(db, "UPDATE team_department_state SET locked_until = now() - interval '1 second'")

    r = await _ask(client, "Apologies for earlier. What changed in the QA budget?")
    assert r.status_code == 200 and r.json()["kind"] == "answer"
    assert "Earlier you ended an interview with this team" in (
        fake.calls_for("answer")[-1].prompt.final_user
    )
    dept = await _dept(db)
    assert dept.strikes == 0 and dept.locked_until is None  # a clean slate on strikes
    assert len(await _flags(db)) == 3  # but the log keeps all three for scoring


async def test_a_failed_model_call_moves_no_trust(seeded, make_client, fake, db):
    fake.queues["classify"].append(HOSTILE)
    fake.queues["answer"] += [ProviderError("overload", retryable=True)]
    client = await _team(make_client, seeded)
    assert (await _ask(client, "Liar!")).status_code == 503
    state = await _state(db)
    assert state.trust == 50 and state.questions_used == 0
    assert (await _dept(db)).strikes == 0 and await _flags(db) == []


async def test_a_retried_question_with_cards_replays(seeded, make_client, fake, db):
    # step 10 found: replaying a question whose answer had cards crashed (wrong _card call)
    fake.queues["classify"].append(POLITE)
    fake.queues["answer"].append(
        {"reply": "We trimmed QA spend to protect our margin.", "cited": ["A-FIN-002#1"]}
    )
    client = await _team(make_client, seeded)
    rid = uuid.uuid4()
    first = (await _ask(client, request_id=rid)).json()
    again = await _ask(client, request_id=rid)
    assert again.status_code == 200
    assert again.json()["evidence"][0]["excerpt"] == first["evidence"][0]["excerpt"]
    assert again.json()["questions_remaining"] == 9  # charged once
    async with make_sessionmaker(db)() as s:
        assert await s.scalar(select(Message.id).where(Message.role == "notice")) is None
