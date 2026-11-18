"""Alliances (cross-company, mutual lock, organiser pairing, partner evidence) and the
Audit Findings Sheet (versions, server-side deadline, citations, the cross-company rule)."""

import uuid

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import make_sessionmaker

API = "/api/v1"
TOKEN = "s" * 40
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


async def _more_teams(org, make_client, seeded):
    """A02 (company A) and B02 (company B) next to the fixture's A01 and B01."""
    r = await org.post(f"{API}/admin/teams", json={"names": ["Extra A", "Extra B"]}, headers=AUTH)
    codes = {t["code"]: t["access_code"] for t in r.json()}
    t1, t2 = list(codes)
    await org.put(f"{API}/admin/teams/{t1}/company", json={"company": "A"}, headers=AUTH)
    await org.put(f"{API}/admin/teams/{t2}/company", json={"company": "B"}, headers=AUTH)
    return t1, codes[t1], t2, codes[t2]


async def _show(db, code, *passages):
    """Pretend the team was shown these passages in an interview."""
    async with make_sessionmaker(db)() as s, s.begin():
        team_id = (await s.execute(text("SELECT id FROM team WHERE code=:c"), {"c": code})).scalar()
        sid = "cfo" if passages[0].startswith("A-") else "kestrel_qc"
        mid = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO message (id, team_id, stakeholder_id, seq, role, status, text) "
                "VALUES (:m, :t, :s, 1000, 'answer', 'answered', 'x')"
            ),
            {"m": mid, "t": team_id, "s": sid},
        )
        for p in passages:
            await s.execute(
                text(
                    "INSERT INTO team_disclosure "
                    "(team_id, passage_id, stakeholder_id, message_id, mode) "
                    "VALUES (:t, :p, :s, :m, 'quote')"
                ),
                {"t": team_id, "p": p, "s": sid, "m": mid},
            )


async def _start(org, key):
    r = await org.post(f"{API}/admin/phases/{key}/start", headers=AUTH)
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ alliances
async def test_alliance_needs_both_sides_and_then_locks(seeded, make_client, admin_on, db):
    org = make_client()
    a01 = await _login(make_client, seeded["A01"])
    b01 = await _login(make_client, seeded["B01"])

    r = await a01.post(f"{API}/alliance/requests", json={"team": "B01"})
    assert r.json()["error"]["code"] == "ALLIANCES_CLOSED"  # solo doesn't allow alliances
    await _start(org, "alliance_window")

    assert [t["code"] for t in (await a01.get(f"{API}/alliance/teams")).json()] == ["B01"]
    r = await a01.post(f"{API}/alliance/requests", json={"team": "B01"})
    assert r.status_code == 201
    rid = r.json()["id"]
    assert (await a01.post(f"{API}/alliance/requests", json={"team": "B01"})).json()["error"][
        "code"
    ] == "REQUEST_EXISTS"

    mine = (await b01.get(f"{API}/alliance")).json()
    assert [x["teams"][0]["code"] for x in mine["incoming"]] == ["A01"] and mine["locked"] is None
    assert (await a01.get(f"{API}/alliance")).json()["outgoing"][0]["id"] == rid

    # before the lock, nothing is shared
    await _show(db, "B01", "B-QC-001#1")
    assert (await a01.get(f"{API}/alliance/evidence")).json() == []

    r = await b01.post(f"{API}/alliance/requests/{rid}/accept")
    assert r.json()["locked"]["teams"][0]["code"] == "A01"
    shared = (await a01.get(f"{API}/alliance/evidence")).json()
    assert [(e["passage_id"], e["team_code"]) for e in shared] == [("B-QC-001#1", "B01")]

    # locked for good: no withdrawing, and nobody else can claim them
    assert (await a01.delete(f"{API}/alliance/requests/{rid}")).json()["error"][
        "code"
    ] == "ALLIANCE_LOCKED"
    r = await a01.post(f"{API}/alliance/requests", json={"team": "B01"})
    assert r.json()["error"]["code"] == "ALREADY_ALLIED"
    # a partner's evidence can go on the board
    r = await a01.put(
        f"{API}/board",
        json={
            "version": 0,
            "cards": [{"id": "e1", "kind": "evidence", "passage_id": "B-QC-001#1"}],
        },
    )
    assert r.status_code == 200, r.text


async def test_same_company_requests_are_refused(seeded, make_client, admin_on):
    org = make_client()
    a02, a02_code, _, _ = await _more_teams(org, make_client, seeded)
    await _start(org, "alliance_window")
    a01 = await _login(make_client, seeded["A01"])
    r = await a01.post(f"{API}/alliance/requests", json={"team": a02})
    assert r.json()["error"]["code"] == "SAME_COMPANY"


async def test_accepting_one_withdraws_the_other_requests(seeded, make_client, admin_on):
    org = make_client()
    a02, a02_code, b02, b02_code = await _more_teams(org, make_client, seeded)
    await _start(org, "alliance_window")
    a01 = await _login(make_client, seeded["A01"])
    a02c = await _login(make_client, a02_code)
    b01 = await _login(make_client, seeded["B01"])
    first = (await a01.post(f"{API}/alliance/requests", json={"team": "B01"})).json()["id"]
    await a02c.post(f"{API}/alliance/requests", json={"team": "B01"})
    assert len((await b01.get(f"{API}/alliance")).json()["incoming"]) == 2
    await b01.post(f"{API}/alliance/requests/{first}/accept")
    assert (await a02c.get(f"{API}/alliance")).json()["outgoing"] == []  # withdrawn for them


async def test_organisers_pair_three_teams_and_can_dissolve(seeded, make_client, admin_on, db):
    org = make_client()
    a02, _, b02, _ = await _more_teams(org, make_client, seeded)
    r = await org.post(f"{API}/admin/alliances", json={"teams": ["A01", "B01", b02]}, headers=AUTH)
    assert r.status_code == 201 and r.json()["status"] == "locked"
    r = await org.post(f"{API}/admin/alliances", json={"teams": [a02, "B01"]}, headers=AUTH)
    assert r.json()["error"]["code"] == "ALREADY_ALLIED"
    r = await org.post(f"{API}/admin/alliances", json={"teams": ["A01", a02]}, headers=AUTH)
    assert r.json()["error"]["code"] in ("SAME_COMPANY", "ALREADY_ALLIED")

    a01 = await _login(make_client, seeded["A01"])
    me = (await a01.get(f"{API}/alliance")).json()
    assert sorted(t["code"] for t in me["locked"]["teams"]) == sorted(["B01", b02])

    listed = (await org.get(f"{API}/admin/alliances", headers=AUTH)).json()
    await org.delete(f"{API}/admin/alliances/{listed[0]['id']}", headers=AUTH)
    assert (await a01.get(f"{API}/alliance")).json()["locked"] is None


# ------------------------------------------------------------------ submissions
def _finding(fid="f1", sources=("A-FIN-002#1",), cross=False, title="QA budget cut"):
    return {"id": fid, "title": title, "finding": "The QA budget was cut.",
            "impact": "Defects rose.", "recommendation": "Restore it.",
            "sources": list(sources), "cross_company": cross}  # fmt: skip


async def test_sheet_versions_conflicts_and_submit(seeded, make_client, admin_on, db):
    org = make_client()
    a01 = await _login(make_client, seeded["A01"])
    laptop2 = await _login(make_client, seeded["A01"])
    await _show(db, "A01", "A-FIN-002#1")

    sheet = (await a01.get(f"{API}/submission")).json()
    assert sheet["round"] == "solo" and sheet["open"] and sheet["version"] == 0

    r = await a01.put(
        f"{API}/submission", json={"base_version": 0, "summary": "draft", "findings": []}
    )
    assert r.json()["version"] == 1 and not r.json()["final"]
    # the other laptop, still on version 0, is told to reload instead of overwriting
    r = await laptop2.put(f"{API}/submission", json={"base_version": 0, "findings": []})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUBMISSION_CONFLICT"

    r = await a01.put(
        f"{API}/submission",
        json={"base_version": 1, "findings": [_finding(sources=["B-QC-001#1"])]},
    )
    assert r.json()["error"]["code"] == "UNKNOWN_EVIDENCE"  # never shown to this team

    r = await a01.put(
        f"{API}/submission",
        json={"base_version": 1, "findings": [_finding(sources=[])], "final": True},
    )
    assert r.json()["error"]["code"] == "SHEET_INCOMPLETE"
    r = await a01.put(
        f"{API}/submission", json={"base_version": 1, "findings": [_finding()], "final": True}
    )
    assert r.status_code == 200 and r.json()["final"] and r.json()["version"] == 2
    assert [v["final"] for v in r.json()["versions"]] == [False, True]
    old = (await a01.get(f"{API}/submission/versions/1")).json()
    assert old["summary"] == "draft" and old["findings"] == []

    listed = (
        await org.get(f"{API}/admin/submissions", params={"round": "solo"}, headers=AUTH)
    ).json()
    row = next(t for t in listed["teams"] if t["team"] == "A01")
    assert row["versions"] == 2 and row["submitted_at"] is not None
    full = (await org.get(f"{API}/admin/submissions/A01", headers=AUTH)).json()
    assert full["sheet"]["findings"][0]["title"] == "QA budget cut" and not full["never_submitted"]


async def test_the_deadline_is_the_servers(seeded, make_client, admin_on, db):
    a01 = await _login(make_client, seeded["A01"])
    async with db.begin() as conn:  # solo (30 min) ran out a second ago
        await conn.execute(text("UPDATE phase SET started_at = now() - interval '1801 seconds' "
                                "WHERE key = 'solo'"))  # fmt: skip
    r = await a01.put(f"{API}/submission", json={"base_version": 0, "findings": []})
    assert r.json()["error"]["code"] == "SUBMISSIONS_CLOSED"
    org = make_client()
    await org.post(f"{API}/admin/teams/A01/time", json={"seconds": 60}, headers=AUTH)
    r = await a01.put(f"{API}/submission", json={"base_version": 0, "findings": []})
    assert r.status_code == 200  # the time given back counts for submissions too


async def test_allied_teams_need_a_cross_company_finding(seeded, make_client, admin_on, db):
    org = make_client()
    await org.post(f"{API}/admin/alliances", json={"teams": ["A01", "B01"]}, headers=AUTH)
    await _show(db, "A01", "A-FIN-002#1")
    await _show(db, "B01", "B-QC-001#1")
    a01 = await _login(make_client, seeded["A01"])
    assert (await a01.get(f"{API}/submission")).json()["requires_cross_company"] is True
    r = await a01.put(
        f"{API}/submission", json={"base_version": 0, "findings": [_finding()], "final": True}
    )
    assert r.json()["error"]["code"] == "SHEET_INCOMPLETE"
    assert "cross-company" in r.json()["error"]["message"]
    both = _finding("f2", sources=["A-FIN-002#1", "B-QC-001#1"], cross=True, title="Joint cause")
    r = await a01.put(
        f"{API}/submission",
        json={"base_version": 0, "findings": [_finding(), both], "final": True},
    )
    assert r.status_code == 200, r.text
