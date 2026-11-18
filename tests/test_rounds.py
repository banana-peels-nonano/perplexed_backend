"""Organisers manage rounds, hand out documents per round, and tell teams where they stand."""

import base64
import uuid

import pytest

from app.config import get_settings

API = "/api/v1"
TOKEN = "r" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def admin_on(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _login(make_client, access_code):
    client = make_client()
    r = await client.post(f"{API}/auth/login", json={"access_code": access_code})
    assert r.status_code == 200, r.text
    return client


async def _keys(org):
    return [p["key"] for p in (await org.get(f"{API}/admin/phases", headers=AUTH)).json()]


# ------------------------------------------------------------------ rounds
async def test_organisers_add_rename_reorder_and_delete_rounds(seeded, make_client, admin_on):
    org = make_client()
    before = await _keys(org)
    assert before == ["briefing", "solo", "alliance_window", "joint", "submission_closed",
                      "round2"]  # fmt: skip
    for n in (3, 4, 5):  # a fifth round, nobody planned for, still works
        r = await org.post(
            f"{API}/admin/phases",
            json={"key": f"round{n}", "name": f"Round {n}", "planned_seconds": 1200},
            headers=AUTH,
        )
        assert r.status_code == 201, r.text
    r = await org.post(
        f"{API}/admin/phases",
        json={"key": "lunch", "name": "Lunch", "capabilities": {"interview": False},
              "after": "solo"},
        headers=AUTH,
    )  # fmt: skip
    assert (await _keys(org))[:3] == ["briefing", "solo", "lunch"]
    assert (await _keys(org))[-3:] == ["round3", "round4", "round5"]

    r = await org.patch(f"{API}/admin/phases/round5", json={"name": "Finals"}, headers=AUTH)
    assert r.json()["name"] == "Finals"
    r = await org.post(f"{API}/admin/phases", json={"key": "solo", "name": "x"}, headers=AUTH)
    assert r.json()["error"]["code"] == "PHASE_EXISTS"
    r = await org.post(f"{API}/admin/phases", json={"key": "current", "name": "x"}, headers=AUTH)
    assert r.json()["error"]["code"] == "BAD_KEY"
    r = await org.post(
        f"{API}/admin/phases",
        json={"key": "x1", "name": "x", "capabilities": {"teleport": True}},
        headers=AUTH,
    )
    assert r.status_code == 422

    keys = await _keys(org)
    new_order = keys[:-2] + [keys[-1], keys[-2]]
    r = await org.put(f"{API}/admin/phases/order", json={"keys": new_order}, headers=AUTH)
    assert [p["key"] for p in r.json()] == new_order
    r = await org.put(f"{API}/admin/phases/order", json={"keys": keys[:-1]}, headers=AUTH)
    assert r.json()["error"]["code"] == "BAD_ORDER"

    assert (await org.delete(f"{API}/admin/phases/lunch", headers=AUTH)).status_code == 200
    r = await org.delete(f"{API}/admin/phases/solo", headers=AUTH)  # running in the fixture
    assert r.json()["error"]["code"] == "PHASE_USED"
    r = await org.delete(f"{API}/admin/phases/joint", headers=AUTH)  # the case unlocks in it
    assert r.json()["error"]["code"] == "PHASE_USED"
    seqs = [p["seq"] for p in (await org.get(f"{API}/admin/phases", headers=AUTH)).json()]
    assert seqs == list(range(len(seqs)))

    # a team sees the same order, and an added round can run interviews
    team = await _login(make_client, seeded["A01"])
    assert [r["key"] for r in (await team.get(f"{API}/rounds")).json()] == await _keys(org)
    await org.post(f"{API}/admin/phases/round3/start", headers=AUTH)
    status = (await team.get(f"{API}/status")).json()
    assert status["phase"]["key"] == "round3" and status["phase"]["interviews_open"]


async def test_an_added_round_counts_for_phase_unlocks(seeded, make_client, fake, admin_on):
    # the case unlocks some evidence from 'joint'; a round added after it inherits that,
    # and asking in a round the case file never named doesn't break retrieval
    org = make_client()
    await org.post(f"{API}/admin/phases", json={"key": "round3", "name": "R3"}, headers=AUTH)
    await org.post(f"{API}/admin/phases/round3/start", headers=AUTH)
    team = await _login(make_client, seeded["A01"])
    r = await team.post(
        f"{API}/stakeholders/cfo/questions",
        json={"client_request_id": str(uuid.uuid4()), "text": "What happened to QA spend?"},
    )
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ documents
async def test_documents_reach_only_their_audience_when_released(seeded, make_client, admin_on):
    org = make_client()
    a01 = await _login(make_client, seeded["A01"])
    b01 = await _login(make_client, seeded["B01"])

    async def add(**body):
        r = await org.post(f"{API}/admin/documents", json=body, headers=AUTH)
        assert r.status_code == 201, r.text
        return r.json()

    everyone = await add(title="Rules", body="# Rules\nBe kind.", release_now=True)
    northfield = await add(title="Org chart", body="CFO, ops, QA.", company="A", release_now=True)
    pdf = base64.b64encode(b"%PDF-1.4 fake").decode()
    only_b01 = await add(
        title="Kestrel memo",
        file={"filename": "memo.pdf", "content_type": "application/pdf", "data_base64": pdf},
        teams=["B01"],
        release_now=True,
    )
    draft = await add(title="Round 2 pack", body="later")
    at_round = await add(title="Joint brief", body="for joint", round="joint")
    assert not draft["visible"] and not at_round["visible"]

    titles = lambda r: [d["title"] for d in r.json()]  # noqa: E731
    assert titles(await a01.get(f"{API}/documents")) == ["Org chart", "Rules"]
    assert titles(await b01.get(f"{API}/documents")) == ["Kestrel memo", "Rules"]

    body = (await a01.get(f"{API}/documents/{everyone['id']}")).json()
    assert body == {"id": everyone["id"], "title": "Rules", "body": "# Rules\nBe kind."}
    f = await b01.get(f"{API}/documents/{only_b01['id']}")
    assert f.content == b"%PDF-1.4 fake" and f.headers["content-type"] == "application/pdf"
    # not for you, or not released: indistinguishable from not existing
    assert (await a01.get(f"{API}/documents/{only_b01['id']}")).status_code == 404
    assert (await b01.get(f"{API}/documents/{northfield['id']}")).status_code == 404
    assert (await a01.get(f"{API}/documents/{draft['id']}")).status_code == 404

    await org.patch(f"{API}/admin/documents/{draft['id']}", json={"released": True}, headers=AUTH)
    await org.post(f"{API}/admin/phases/joint/start", headers=AUTH)  # releases "Joint brief"
    assert titles(await a01.get(f"{API}/documents")) == [
        "Joint brief", "Round 2 pack", "Org chart", "Rules"
    ]  # fmt: skip
    await org.delete(f"{API}/admin/documents/{everyone['id']}", headers=AUTH)
    assert "Rules" not in titles(await a01.get(f"{API}/documents"))

    r = await org.post(f"{API}/admin/documents", json={"title": "x"}, headers=AUTH)
    assert r.json()["error"]["code"] == "BAD_DOCUMENT"
    r = await org.post(
        f"{API}/admin/documents", json={"title": "x", "body": "y", "teams": ["Z99"]}, headers=AUTH
    )
    assert r.status_code == 404


# ------------------------------------------------------------------ round status
async def test_round_results_are_drafted_then_published_together(seeded, make_client, admin_on):
    org = make_client()
    a01 = await _login(make_client, seeded["A01"])
    b01 = await _login(make_client, seeded["B01"])
    r = await org.put(
        f"{API}/admin/phases/round2/statuses",
        json={"entries": [
            {"team": "A01", "status": "advancing", "message": "See you in round 2."},
            {"team": "B01", "status": "eliminated", "message": "Thanks for playing."},
        ]},
        headers=AUTH,
    )  # fmt: skip
    assert r.json() == {"round": "round2", "saved": 2, "published": 0}

    mine = lambda rounds: {x["key"]: x["my_status"] for x in rounds.json()}  # noqa: E731
    assert mine(await a01.get(f"{API}/rounds"))["round2"] is None  # drafts are invisible

    r = await org.post(f"{API}/admin/phases/round2/statuses/publish", headers=AUTH)
    assert r.json()["published"] == 2
    assert mine(await a01.get(f"{API}/rounds"))["round2"]["status"] == "advancing"
    out = mine(await b01.get(f"{API}/rounds"))["round2"]
    assert out["status"] == "eliminated" and out["message"] == "Thanks for playing."

    listed = (await org.get(f"{API}/admin/phases/round2/statuses", headers=AUTH)).json()
    assert {x["team"]: (x["status"], x["published"]) for x in listed} == {
        "A01": ("advancing", True),
        "B01": ("eliminated", True),
    }

    # an eliminated team can't interview in that round; the advancing one can
    await org.post(f"{API}/admin/phases/round2/start", headers=AUTH)
    await org.patch(
        f"{API}/admin/phases/round2", json={"capabilities": {"interview": True}}, headers=AUTH
    )
    assert (await b01.get(f"{API}/status")).json()["phase"]["interviews_open"] is False
    r = await b01.post(
        f"{API}/stakeholders/kestrel_qc/questions",
        json={"client_request_id": str(uuid.uuid4()), "text": "Hello?"},
    )
    assert r.json()["error"]["code"] == "NOT_IN_ROUND"
    assert (await a01.get(f"{API}/status")).json()["phase"]["interviews_open"] is True
