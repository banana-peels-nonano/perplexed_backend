"""Step 11: the organiser API: phases, the team view, fixes, live config, content reload."""

import uuid

import pytest
from sqlalchemy import text

from app.config import get_settings

API = "/api/v1"
TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HOSTILE = {"label": "hostile", "topics": ["qa_budget"], "respectful": False}
QA = {"label": "specific", "topics": ["qa_budget"], "respectful": True}


@pytest.fixture
def admin_on(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _team(make_client, seeded, code="A01"):
    client = make_client()
    r = await client.post(f"{API}/auth/login", json={"access_code": seeded[code]})
    assert r.status_code == 200
    return client


async def _ask(client, question="What happened to QA spend?", who="cfo"):
    return await client.post(
        f"{API}/stakeholders/{who}/questions",
        json={"client_request_id": str(uuid.uuid4()), "text": question},
    )


# ------------------------------------------------------------------ access
async def test_organiser_api_is_absent_without_a_token(seeded, make_client, monkeypatch):
    # an empty variable beats a token in .env (deleting the variable wouldn't: the settings
    # would fall back to .env, which is what failed on a machine with ADMIN_TOKEN set)
    monkeypatch.setenv("ADMIN_TOKEN", "")
    get_settings.cache_clear()
    client = make_client()
    r = await client.get(f"{API}/admin/overview", headers=AUTH)
    assert r.status_code == 404  # looks like it doesn't exist
    get_settings.cache_clear()


async def test_wrong_token_or_team_session_gets_nothing(seeded, make_client, admin_on):
    team = await _team(make_client, seeded)  # a logged-in team...
    assert (await team.get(f"{API}/admin/teams")).status_code == 401  # ...isn't an organiser
    bad = {"Authorization": "Bearer " + "x" * 40}
    assert (await team.get(f"{API}/admin/teams", headers=bad)).status_code == 401
    assert (await team.get(f"{API}/admin/teams", headers=AUTH)).status_code == 200


# ------------------------------------------------------------------ phases
async def test_phase_controls_drive_what_teams_can_do(seeded, make_client, fake, admin_on):
    org = make_client()
    team = await _team(make_client, seeded)
    assert (await _ask(team)).status_code == 200  # solo is running (seed)

    r = await org.post(f"{API}/admin/phases/current/pause", headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "paused"
    assert (await _ask(team)).json()["error"]["code"] == "PHASE_CLOSED"
    status = (await team.get(f"{API}/status")).json()
    assert status["phase"]["status"] == "paused" and not status["phase"]["interviews_open"]

    r = await org.post(f"{API}/admin/phases/current/resume", headers=AUTH)
    assert r.json()["status"] == "running"
    before = r.json()["remaining_seconds"]
    r = await org.post(f"{API}/admin/phases/current/extend", json={"seconds": 300}, headers=AUTH)
    assert r.json()["remaining_seconds"] >= before + 299
    assert (await _ask(team)).status_code == 200

    # starting the next phase ends this one; briefing has no interviews
    r = await org.post(f"{API}/admin/phases/briefing/start", headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "running"
    listed = {p["key"]: p for p in (await org.get(f"{API}/admin/phases", headers=AUTH)).json()}
    assert listed["solo"]["status"] == "ended"
    assert (await _ask(team)).json()["error"]["code"] == "PHASE_CLOSED"

    r = await org.post(f"{API}/admin/phases/current/end", headers=AUTH)
    assert r.json()["status"] == "ended"
    assert (await team.get(f"{API}/status")).json()["phase"] is None
    r = await org.post(f"{API}/admin/phases/current/pause", headers=AUTH)
    assert r.json()["error"]["code"] == "NO_ACTIVE_PHASE"


async def test_a_timed_phase_closes_interviews_when_its_time_runs_out(
    seeded, make_client, fake, admin_on, db
):
    async with db.begin() as conn:  # solo started two hours ago, planned 30 min
        await conn.execute(text("UPDATE phase SET started_at = now() - interval '2 hours' "
                                "WHERE key = 'solo'"))  # fmt: skip
    team = await _team(make_client, seeded)
    assert (await _ask(team)).json()["error"]["code"] == "PHASE_CLOSED"
    phase = (await team.get(f"{API}/status")).json()["phase"]
    assert phase["remaining_seconds"] == 0 and phase["interviews_open"] is False


# ------------------------------------------------------------------ the team view
async def test_teams_view_shows_what_participants_never_see(seeded, make_client, fake, admin_on):
    team = await _team(make_client, seeded)
    fake.queues["classify"] += [HOSTILE, HOSTILE, HOSTILE]
    for _ in range(3):
        await _ask(team, "You liars.")
    org = make_client()
    rows = {t["code"]: t for t in (await org.get(f"{API}/admin/teams", headers=AUTH)).json()}
    a01 = rows["A01"]
    cfo = next(s for s in a01["stakeholders"] if s["stakeholder_id"] == "cfo")
    assert cfo["trust"] == 5 and cfo["strikes"] == 3 and cfo["locked_for_seconds"] > 0
    assert cfo["tier"] == "guarded"
    assert a01["hr_walkouts"] == 1 and a01["questions_asked"] == 3
    assert a01["active_sessions"] == 1 and rows["B01"]["questions_asked"] == 0

    thread = (await org.get(f"{API}/admin/teams/A01/threads/cfo", headers=AUTH)).json()
    first_q = thread[0]
    assert first_q["role"] == "question" and first_q["meta"]["label"] == "hostile"
    assert first_q["meta"]["trust_delta"] == -15 and first_q["meta"]["strike"] is True
    assert thread[-1]["role"] == "notice" and thread[-2]["meta"]["walked_out"] is True

    # organisers can clear the lockout and restore trust (e.g. after a dispute)
    r = await org.patch(
        f"{API}/admin/teams/A01/stakeholders/cfo",
        json={"trust": 50, "clear_lockout": True},
        headers=AUTH,
    )
    assert r.json()["locked_for_seconds"] == 0 and r.json()["strikes"] == 0
    fake.queues["classify"].append(QA)
    assert (await _ask(team, "Apologies. What changed in QA?")).status_code == 200


async def test_deflections_are_visible_to_organisers_only(seeded, make_client, fake, admin_on):
    team = await _team(make_client, seeded)
    leak = {"reply": "It was cut by 15 percent.", "cited": []}
    fake.queues["classify"].append(QA)
    fake.queues["answer"] += [leak, leak]
    body = (await _ask(team, "How much was the QA cut?")).json()
    assert "status" not in body  # participants: just an answer
    org = make_client()
    thread = (await org.get(f"{API}/admin/teams/A01/threads/cfo", headers=AUTH)).json()
    assert thread[1]["status"] == "blocked" and thread[0]["meta"]["deflected"] is True
    assert len(thread[0]["meta"]["failed_checks"]) == 2
    overview = (await org.get(f"{API}/admin/overview", headers=AUTH)).json()
    assert overview["deflections"] == 1 and overview["phase"]["key"] == "solo"


# ------------------------------------------------------------------ quota and config
async def test_quota_and_live_config(seeded, make_client, fake, admin_on):
    org = make_client()
    r = await org.post(f"{API}/admin/quota", json={"quota": 12}, headers=AUTH)
    assert r.json()["rows_updated"] > 0
    team = await _team(make_client, seeded)
    listed = (await team.get(f"{API}/stakeholders")).json()
    assert {s["questions_remaining"] for s in listed} == {12}

    r = await org.patch(
        f"{API}/admin/config",
        json={"hr": {"strikes_to_lockout": 1}, "max_passages": 3},
        headers=AUTH,
    )
    assert r.status_code == 200
    config = (await org.get(f"{API}/admin/config", headers=AUTH)).json()
    assert config["hr"]["strikes_to_lockout"] == 1 and config["hr"]["lockout_seconds"] == 600
    assert config["max_passages"] == 3 and config["tier_thresholds"]["open"] == 70  # kept
    fake.queues["classify"].append(HOSTILE)
    assert (await _ask(team, "Liar.")).json()["kind"] == "hr_notice"  # takes effect live

    bad = [{"tier_thresholds": {"neutral": 80, "open": 70}}, {"max_passages": 50},
           {"secret_key": 1}, {"hr": {"strikes_to_lockout": 0}}]  # fmt: skip
    for body in bad:
        assert (await org.patch(f"{API}/admin/config", json=body, headers=AUTH)).status_code == 422


# ------------------------------------------------------------------ content reload
async def test_content_reload(seeded, make_client, admin_on):
    org = make_client()
    r = await org.post(
        f"{API}/admin/content/reload", json={"path": "tests/fixtures/northfield.toml"}, headers=AUTH
    )
    assert r.status_code == 200 and r.json()["passages"] == 27
    for path in [
        "../../etc/passwd",
        "app/config.py",
        ".env",
        "pyproject.toml",
        "tests/fixtures/../../pyproject.toml",
    ]:
        r = await org.post(f"{API}/admin/content/reload", json={"path": path}, headers=AUTH)
        assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_PATH", path
    r = await org.post(
        f"{API}/admin/content/reload",
        json={"path": "tests/fixtures/northfield_redteam.toml"},
        headers=AUTH,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CASE"
