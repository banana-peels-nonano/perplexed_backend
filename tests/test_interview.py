"""Step 6: one question end to end, through the real endpoint, with the fake model.

Each test scripts the fake: what the classifier "decides" and what the stakeholder "says".
Then it checks what OUR code did with that: prompts, evidence, quotas, disclosures, refunds.
"""

import asyncio
import uuid

from sqlalchemy import func, select, text

from app.db import make_sessionmaker
from app.llm.base import ProviderError
from app.models import LlmCall, Message, TeamDisclosure

API = "/api/v1"
QA_BUDGET = {"label": "specific", "topics": ["qa_budget"], "respectful": True}
MEMO_TEXT = (
    "As part of the FY cost programme, the quality-assurance budget is reduced. "
    "Approved: R. Malhotra, CFO. Rationale: protect the 9 percent operating margin target."
)


async def _team(make_client, seeded, code="A01"):
    client = make_client()
    r = await client.post(f"{API}/auth/login", json={"access_code": seeded[code]})
    assert r.status_code == 200
    return client


async def _ask(client, stakeholder, question, request_id=None):
    return await client.post(
        f"{API}/stakeholders/{stakeholder}/questions",
        json={"client_request_id": str(request_id or uuid.uuid4()), "text": question},
    )


async def _count(db, model, *where):
    async with make_sessionmaker(db)() as s:
        return await s.scalar(select(func.count()).select_from(model).where(*where))


async def _sql(db, statement):
    async with db.begin() as conn:
        await conn.execute(text(statement))


async def test_question_returns_answer_and_authentic_evidence_card(seeded, make_client, fake, db):
    fake.queues["classify"].append(QA_BUDGET)
    fake.queues["answer"].append(
        {"reply": "We trimmed some discretionary QA spend.", "cited": ["A-FIN-002#1"]}
    )
    client = await _team(make_client, seeded)
    r = await _ask(client, "cfo", "What happened to the QA budget this year?")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "We trimmed some discretionary QA spend."
    assert body["status"] == "answered"
    assert body["questions_remaining"] == 9
    [card] = body["evidence"]
    assert card["passage_id"] == "A-FIN-002#1" and card["new"] is True
    assert card["excerpt"] == MEMO_TEXT  # from the database, not the model
    assert card["record_title"] == "Cost programme memo: QA budget"

    # what reached the model: the memo (neutral trust -> paraphrase), never the 15% figure
    prompt = fake.calls_for("answer")[0].prompt.final_user
    assert 'id="A-FIN-002#1"' in prompt and 'mode="paraphrase"' in prompt
    assert "15 percent" not in prompt
    # recorded: 2 messages, 1 discovery, 2 model calls with costs
    assert await _count(db, Message) == 2
    assert await _count(db, TeamDisclosure, TeamDisclosure.passage_id == "A-FIN-002#1") == 1
    assert await _count(db, LlmCall, LlmCall.status == "ok") == 2


async def test_stakeholder_remembers_and_second_laptop_sees_the_thread(seeded, make_client, fake):
    revenue = {"label": "specific", "topics": ["revenue"], "respectful": True}
    fake.queues["classify"] += [revenue, revenue]
    # the figure is only allowed because the revenue passage is in this turn's evidence
    fake.queues["answer"] += [
        {"reply": "Revenue fell to 41.7 crore by Q3.", "cited": ["A-FIN-001#1"]},
        {"reply": "As I said, 41.7 crore.", "cited": ["A-FIN-001#1"]},
    ]
    laptop1 = await _team(make_client, seeded)
    await _ask(laptop1, "cfo", "How has revenue moved?")
    await _ask(laptop1, "cfo", "What was the figure you just gave me?")

    second_prompt = fake.calls_for("answer")[1].prompt
    assert "How has revenue moved?" in second_prompt.history[0].content
    assert second_prompt.history[1].content == "Revenue fell to 41.7 crore by Q3."

    laptop2 = await _team(make_client, seeded)  # same team, other laptop
    thread = (await laptop2.get(f"{API}/stakeholders/cfo/messages")).json()
    assert [m["role"] for m in thread] == ["question", "answer", "question", "answer"]
    assert thread[3]["text"] == "As I said, 41.7 crore."
    assert thread[1]["evidence"][0]["passage_id"] == "A-FIN-001#1"


async def test_leaking_reply_is_regenerated_then_replaced_by_a_deflection(
    seeded, make_client, fake, db
):
    fake.queues["classify"].append(QA_BUDGET)
    leak = {"reply": "Honestly? The QA budget fell by 15 percent.", "cited": []}
    fake.queues["answer"] += [leak, leak]  # leaks twice
    client = await _team(make_client, seeded)
    body = (await _ask(client, "cfo", "How much was the QA budget cut?")).json()
    assert body["status"] == "blocked"
    assert "15 percent" not in body["answer"]
    assert body["evidence"] == []
    assert len(fake.calls_for("answer")) == 2  # one regeneration, then stop
    assert "Before you answer" in fake.calls_for("answer")[1].prompt.final_user
    assert await _count(db, TeamDisclosure) == 0  # nothing counts as discovered
    assert body["questions_remaining"] == 9  # the question was still asked


async def test_model_failure_refunds_the_question_and_a_retry_works(seeded, make_client, fake, db):
    fake.queues["answer"].append(ProviderError("simulated overload", retryable=True))
    client = await _team(make_client, seeded)
    request_id = uuid.uuid4()
    r = await _ask(client, "cfo", "Revenue?", request_id)
    assert r.status_code == 503 and r.json()["error"]["code"] == "PROVIDER_BUSY"
    assert await _count(db, Message) == 0
    listed = (await client.get(f"{API}/stakeholders/cfo")).json()
    assert listed["questions_remaining"] == 10  # refunded

    retry = await _ask(client, "cfo", "Revenue?", request_id)  # same id works after a refund
    assert retry.status_code == 200 and retry.json()["questions_remaining"] == 9


async def test_retrying_the_same_request_never_charges_twice(seeded, make_client, fake):
    client = await _team(make_client, seeded)
    request_id = uuid.uuid4()
    first = (await _ask(client, "cfo", "Revenue?", request_id)).json()
    again = (await _ask(client, "cfo", "Revenue?", request_id)).json()
    assert again["answer_id"] == first["answer_id"]
    assert again["questions_remaining"] == first["questions_remaining"] == 9
    assert len(fake.calls_for("answer")) == 1


async def test_one_question_in_flight_per_stakeholder_even_from_many_laptops(
    seeded, make_client, fake
):
    fake.delay = 0.3  # keep the first question "thinking" while the others arrive
    client = await _team(make_client, seeded)
    results = await asyncio.gather(*(_ask(client, "cfo", f"Question {i}") for i in range(5)))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409, 409]
    rejected = [r.json()["error"]["code"] for r in results if r.status_code == 409]
    assert set(rejected) == {"QUESTION_IN_FLIGHT"}


async def test_quota_runs_out(seeded, make_client, db):
    await _sql(db, "UPDATE team_stakeholder_state SET quota = 1")
    client = await _team(make_client, seeded)
    assert (await _ask(client, "cfo", "One")).status_code == 200
    r = await _ask(client, "cfo", "Two")
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUOTA_EXHAUSTED"


async def test_other_company_and_closed_phase_are_refused(seeded, make_client, db):
    kestrel = await _team(make_client, seeded, "B01")
    r = await _ask(kestrel, "cfo", "Hello?")
    assert r.status_code == 404  # Northfield's CFO doesn't exist for a Kestrel team

    await _sql(db, "UPDATE phase SET status = 'scheduled'")
    northfield = await _team(make_client, seeded)
    r = await _ask(northfield, "cfo", "Hello?")
    assert r.status_code == 409 and r.json()["error"]["code"] == "PHASE_CLOSED"


async def test_disclosure_unlocks_the_deleted_message_for_later_questions(
    seeded, make_client, fake, db
):
    await _sql(db, "UPDATE team_stakeholder_state SET trust = 80 WHERE stakeholder_id = 'qa_lead'")
    retests = {"label": "specific", "topics": ["retests"], "respectful": True}
    fake.queues["classify"] += [retests, retests]
    fake.queues["answer"] += [
        {"reply": "The log shows lots released per shift instruction.", "cited": ["A-OPS-002#2"]},
        {"reply": "There was a message. It was deleted.", "cited": ["A-COM-001#3"]},
    ]
    client = await _team(make_client, seeded)

    await _ask(client, "qa_lead", "Were failed lots released without retests?")
    first_prompt = fake.calls_for("answer")[0].prompt.final_user
    assert "A-COM-001#3" not in first_prompt  # locked until the release log is disclosed

    body = (await _ask(client, "qa_lead", "Who told you to skip the retests?")).json()
    second_prompt = fake.calls_for("answer")[1].prompt.final_user
    assert 'id="A-COM-001#3"' in second_prompt  # unlocked by the first disclosure
    assert body["evidence"][0]["passage_id"] == "A-COM-001#3"
    assert "Don't log it" in body["evidence"][0]["excerpt"]
