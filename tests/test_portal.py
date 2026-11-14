"""Step 9: what the portal needs besides interviews: status, the evidence list, the board."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.db import make_sessionmaker
from app.models import Message
from app.portal.clock import remaining_seconds

API = "/api/v1"
QA_BUDGET = {"label": "specific", "topics": ["qa_budget"], "respectful": True}


async def _team(make_client, seeded, code="A01"):
    client = make_client()
    r = await client.post(f"{API}/auth/login", json={"access_code": seeded[code]})
    assert r.status_code == 200
    return client


async def _ask_memo(client, fake):
    fake.queues["classify"].append(QA_BUDGET)
    fake.queues["answer"].append(
        {"reply": "We trimmed QA spend to protect our margin.", "cited": ["A-FIN-002#1"]}
    )
    r = await client.post(
        f"{API}/stakeholders/cfo/questions",
        json={"client_request_id": str(uuid.uuid4()), "text": "What happened to QA spend?"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ clock
T0 = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)


def _left(**kw):
    base = dict(planned_seconds=3600, extended_seconds=0, total_paused_seconds=0,
                started_at=T0, paused_at=None, now=T0)  # fmt: skip
    return remaining_seconds(**{**base, **kw})


def test_phase_clock():
    assert _left(status="scheduled", started_at=None) == 3600
    assert _left(status="running", now=T0 + timedelta(minutes=10)) == 3000
    assert _left(status="running", now=T0 + timedelta(minutes=10), extended_seconds=300) == 3300
    # paused at 10 min: frozen there however long the pause lasts
    assert _left(status="paused", paused_at=T0 + timedelta(minutes=10),
                 now=T0 + timedelta(hours=3)) == 3000  # fmt: skip
    # resumed after a 5-minute pause, 20 min after the start: 15 min used
    assert _left(status="running", total_paused_seconds=300,
                 now=T0 + timedelta(minutes=20)) == 2700  # fmt: skip
    assert _left(status="running", now=T0 + timedelta(hours=2)) == 0  # never negative
    assert _left(status="ended") == 0


# ------------------------------------------------------------------ status
async def test_status_shows_phase_time_quota_and_evidence_count(seeded, make_client, fake):
    client = await _team(make_client, seeded)
    before = (await client.get(f"{API}/status")).json()
    assert before["team"]["code"] == "A01" and before["team"]["company_id"] == "A"
    assert before["phase"]["key"] == "solo" and before["phase"]["interviews_open"] is True
    assert 0 < before["phase"]["remaining_seconds"] <= 3600 * 3
    assert [s["id"] for s in before["stakeholders"]] == ["cfo", "ops_head", "qa_lead"]
    assert before["evidence_count"] == 0
    assert "trust" not in str(before)  # hidden by the rules

    await _ask_memo(client, fake)
    after = (await client.get(f"{API}/status")).json()
    assert after["evidence_count"] == 1
    cfo = next(s for s in after["stakeholders"] if s["id"] == "cfo")
    assert cfo["questions_remaining"] == 9


async def test_status_between_phases_has_no_phase(seeded, make_client, db):
    async with db.begin() as conn:
        await conn.execute(text("UPDATE phase SET status = 'scheduled'"))
    client = await _team(make_client, seeded)
    assert (await client.get(f"{API}/status")).json()["phase"] is None


# ------------------------------------------------------------------ evidence
async def test_evidence_lists_what_the_team_was_shown_at_the_mode_shown(seeded, make_client, fake):
    client = await _team(make_client, seeded)
    await _ask_memo(client, fake)
    [item] = (await client.get(f"{API}/evidence")).json()
    assert item["passage_id"] == "A-FIN-002#1" and item["mode"] == "paraphrase"
    assert "[withheld]" in item["excerpt"] and "9 percent" not in item["excerpt"]
    assert item["stakeholder_name"] == "Ritu Malhotra"
    assert item["record_title"] == "Cost programme memo: QA budget"

    other = await _team(make_client, seeded, "B01")  # another company sees none of it
    assert (await other.get(f"{API}/evidence")).json() == []


# ------------------------------------------------------------------ board
async def test_board_starts_empty_saves_and_is_shared(seeded, make_client, fake):
    laptop1 = await _team(make_client, seeded)
    laptop2 = await _team(make_client, seeded)
    await _ask_memo(laptop1, fake)
    empty = (await laptop1.get(f"{API}/board")).json()
    assert empty["version"] == 0 and empty["cards"] == []

    board = {
        "version": 0,
        "cards": [
            {"id": "c1", "kind": "evidence", "passage_id": "A-FIN-002#1", "x": 40, "y": 30},
            {"id": "n1", "kind": "note", "text": "Ask Arjun who changed sampling", "x": 300},
        ],
        "links": [{"from": "c1", "to": "n1", "label": "follow up"}],
        "extra": {"hypotheses": [{"title": "QA cut caused the voids"}]},
    }
    r = await laptop1.put(f"{API}/board", json=board)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1

    seen = (await laptop2.get(f"{API}/board")).json()  # the other laptop sees it
    assert seen["version"] == 1 and seen["links"][0]["label"] == "follow up"
    assert seen["extra"]["hypotheses"][0]["title"] == "QA cut caused the voids"


async def test_stale_board_save_is_refused_not_merged(seeded, make_client, fake):
    laptop1 = await _team(make_client, seeded)
    laptop2 = await _team(make_client, seeded)
    note = {"id": "n1", "kind": "note", "text": "first"}
    assert (
        await laptop1.put(f"{API}/board", json={"version": 0, "cards": [note]})
    ).status_code == 200
    # laptop2 edited version 0 too, without reloading: refused, told the current version
    r = await laptop2.put(f"{API}/board", json={"version": 0, "cards": []})
    assert r.status_code == 409 and r.json()["error"]["code"] == "BOARD_CONFLICT"
    assert r.json()["error"]["details"]["current_version"] == 1
    r = await laptop2.put(f"{API}/board", json={"version": 1, "cards": []})
    assert r.status_code == 200 and r.json()["version"] == 2


async def test_board_refuses_evidence_the_team_has_not_been_shown(seeded, make_client):
    client = await _team(make_client, seeded)
    card = {"id": "c1", "kind": "evidence", "passage_id": "A-FIN-002#2"}  # the hidden 15%
    r = await client.put(f"{API}/board", json={"version": 0, "cards": [card]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_EVIDENCE"
    assert (await client.get(f"{API}/board")).json()["version"] == 0  # nothing saved


async def test_quote_text_is_cut_by_the_server_not_sent_by_the_team(seeded, make_client, fake, db):
    client = await _team(make_client, seeded)
    answer = await _ask_memo(client, fake)  # "We trimmed QA spend to protect our margin."
    card = {"id": "q1", "kind": "quote", "message_id": answer["answer_id"], "start": 3,
            "end": 20, "text": "I admit I lied"}  # fmt: skip
    r = await client.put(f"{API}/board", json={"version": 0, "cards": [card]})
    assert r.status_code == 200, r.text
    [saved] = r.json()["cards"]
    assert saved["text"] == "trimmed QA spend " and saved["stakeholder_id"] == "cfo"

    bad_span = {**card, "end": 999}
    r = await client.put(f"{API}/board", json={"version": 1, "cards": [bad_span]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_QUOTE"

    # someone else's answer can't be quoted
    other = await _team(make_client, seeded, "B01")
    r = await other.put(f"{API}/board", json={"version": 0, "cards": [card]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_QUOTE"

    # nor can a deflection (status 'blocked'), which would mark where a secret is
    async with make_sessionmaker(db)() as s, s.begin():
        msg = await s.get(Message, uuid.UUID(answer["answer_id"]))
        msg.status = "blocked"
    r = await client.put(f"{API}/board", json={"version": 1, "cards": [card]})
    assert r.status_code == 422


async def test_board_shape_is_checked(seeded, make_client):
    client = await _team(make_client, seeded)
    note = {"id": "n1", "kind": "note", "text": "x"}
    cases = [
        {"version": 0, "cards": [note, note]},  # duplicate ids
        {"version": 0, "cards": [note], "links": [{"from": "n1", "to": "zz"}]},  # dangling
        {"version": 0, "cards": [{**note, "text": "y" * 1001}]},  # note too long
        {"version": 0, "cards": [{**note, "id": "has space"}]},  # bad id
        {"version": 0, "cards": [note], "extra": {"blob": "z" * 40_000}},  # extra too big
        {"version": 0, "cards": [{**note, "kind": "secret"}]},  # unknown kind
        {"version": 0, "cards": [note], "trust": 99},  # unknown field
    ]
    for body in cases:
        r = await client.put(f"{API}/board", json=body)
        assert r.status_code == 422, body
