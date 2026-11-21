"""The records tab: off by default, a catalogue, or the documents themselves (then citable)."""

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import make_sessionmaker
from tests.test_alliances_submissions import AUTH, TOKEN, _finding, _login


@pytest.fixture
def admin_on(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


API = "/api/v1"


async def _mode(org, mode):
    r = await org.patch(f"{API}/admin/config", json={"records_tab": {"mode": mode}}, headers=AUTH)
    assert r.status_code == 200, r.text


async def test_off_titles_full(seeded, make_client, admin_on, db):
    org = make_client()
    a01 = await _login(make_client, seeded["A01"])
    assert (await a01.get(f"{API}/records")).json() == {"mode": "off", "records": []}

    await _mode(org, "titles")
    tab = (await a01.get(f"{API}/records")).json()
    ids = [r["id"] for r in tab["records"]]
    assert tab["mode"] == "titles" and "A-FIN-002" in ids
    assert all(r["id"].startswith("A-") for r in tab["records"])  # own company only
    assert all(r["passages"] == [] for r in tab["records"])  # titles: no text
    # a catalogue makes nothing citable
    sheet = {"base_version": 0, "findings": [_finding(sources=("A-FIN-002#1",))]}
    r = await a01.put(f"{API}/submission", json=sheet)
    assert r.json()["error"]["code"] == "UNKNOWN_EVIDENCE"

    await _mode(org, "full")
    tab = (await a01.get(f"{API}/records")).json()
    fin2 = next(r for r in tab["records"] if r["id"] == "A-FIN-002")
    assert [p["passage_id"] for p in fin2["passages"]][:1] == ["A-FIN-002#1"]
    assert fin2["passages"][0]["text"]
    com = next(r for r in tab["records"] if r["id"] == "A-COM-001")
    assert "A-COM-001#3" not in {p["passage_id"] for p in com["passages"]}  # deleted text
    # full: tab passages are evidence the team may cite and pin
    assert (await a01.put(f"{API}/submission", json=sheet)).status_code == 200
    board = {"version": 0, "cards": [{"id": "e1", "kind": "evidence", "passage_id": "A-FIN-002#1",
                                      "x": 0, "y": 0}], "links": []}  # fmt: skip
    assert (await a01.put(f"{API}/board", json=board)).status_code == 200
    # ...but not the other company's documents
    other = {"base_version": 1, "findings": [_finding(sources=("B-QC-001#1",))]}
    assert (await a01.put(f"{API}/submission", json=other)).json()["error"]["code"] == (
        "UNKNOWN_EVIDENCE"
    )


async def test_interview_records_never_show_and_partners_share_after_lock(
    seeded,
    make_client,
    admin_on,
    db,
):
    async with make_sessionmaker(db)() as s, s.begin():  # make one record an "in confidence" one
        await s.execute(text("UPDATE record SET kind='interview' WHERE id='A-CAL-001'"))
    org = make_client()
    await _mode(org, "full")
    a01 = await _login(make_client, seeded["A01"])
    tab = (await a01.get(f"{API}/records")).json()
    assert "A-CAL-001" not in {r["id"] for r in tab["records"]}
    assert not any(r["partner"] for r in tab["records"])

    r = await org.post(f"{API}/admin/alliances", json={"teams": ["A01", "B01"]}, headers=AUTH)
    assert r.status_code == 201
    tab = (await a01.get(f"{API}/records")).json()
    partner = [r for r in tab["records"] if r["partner"]]
    assert partner and all(r["company_id"] == "B" for r in partner)

    await _mode(org, "off")
    assert (await a01.get(f"{API}/records")).json()["records"] == []


async def test_all_companies_opens_every_companys_records(seeded, make_client, admin_on):
    org = make_client()
    a01 = await _login(make_client, seeded["A01"])
    await org.patch(f"{API}/admin/config", json={"records_tab": {"mode": "full", "all_companies": True}}, headers=AUTH)
    r = await a01.get(f"{API}/records")
    assert r.status_code == 200, r.text
    assert len({x["company_id"] for x in r.json()["records"]}) > 1, r.json()
