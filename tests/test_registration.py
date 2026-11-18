"""Teams sign up with thapar.edu addresses; the access code reaches only their inboxes."""

import re

import pytest

from app import mail
from app.config import get_settings

API = "/api/v1"
TOKEN = "g" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def smtp(monkeypatch):
    """SMTP 'configured', with a recorder instead of a mail server."""
    for k, v in {"ADMIN_TOKEN": TOKEN, "SMTP_HOST": "smtp.test", "SMTP_USER": "u",
                 "SMTP_PASSWORD": "p", "SMTP_FROM": "PERPLEXED <p@test>"}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    sent = []
    from app.api import registration

    registration._recent.clear()
    monkeypatch.setattr(
        mail, "_transport", lambda to, subject, text: sent.append((to, subject, text))
    )
    yield sent
    get_settings.cache_clear()


def _team(name="Night Owls", n=3, start=1):
    return {"team_name": name, "members": [
        {"name": f"Member {i}", "email": f"m{i}_be25@thapar.edu"} for i in range(start, start + n)
    ]}  # fmt: skip


async def _open(org, **rules):
    r = await org.patch(f"{API}/admin/config", json={"registration": {"open": True, **rules}},
                        headers=AUTH)  # fmt: skip
    assert r.status_code == 200, r.text


async def test_register_and_log_in_with_the_emailed_code(seeded, make_client, smtp):
    org, anon = make_client(), make_client()
    assert (await anon.get(f"{API}/registration")).json()["open"] is False
    r = await anon.post(f"{API}/registration", json=_team())
    assert r.json()["error"]["code"] == "REGISTRATION_CLOSED"
    await _open(org)

    r = await anon.post(f"{API}/registration", json=_team())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["team_code"] == "T001" and body["email_status"] == "sent"
    assert "access_code" not in body  # never on screen: only in the inboxes

    ((to, subject, text),) = smtp
    assert to == ["m1_be25@thapar.edu", "m2_be25@thapar.edu", "m3_be25@thapar.edu"]
    assert "Night Owls" in subject
    code = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}\b", text).group(0)
    team = make_client()
    r = await team.post(f"{API}/auth/login", json={"access_code": code})
    assert r.status_code == 200 and r.json()["code"] == "T001"
    assert r.json()["company_id"] is None  # organisers deal companies later


async def test_the_rules_hold(seeded, make_client, smtp):
    org, anon = make_client(), make_client()
    await _open(org, min_members=2, max_members=4)
    bad = _team()
    bad["members"][1]["email"] = "someone@gmail.com"
    r = await anon.post(f"{API}/registration", json=bad)
    assert r.json()["error"]["code"] == "BAD_EMAIL" and r.json()["error"]["details"]["emails"] == [
        "someone@gmail.com"
    ]
    assert (await anon.post(f"{API}/registration", json=_team(n=1))).json()["error"][
        "code"
    ] == "BAD_TEAM_SIZE"
    assert (await anon.post(f"{API}/registration", json=_team(n=5))).json()["error"][
        "code"
    ] == "BAD_TEAM_SIZE"
    dup = _team()
    dup["members"][1]["email"] = "M1_BE25@thapar.edu"  # the same person, different case
    assert (await anon.post(f"{API}/registration", json=dup)).json()["error"]["code"] == "BAD_EMAIL"

    assert (await anon.post(f"{API}/registration", json=_team())).status_code == 201
    r = await anon.post(f"{API}/registration", json=_team("Other", n=2, start=3))  # m3 is taken
    assert r.json()["error"]["code"] == "EMAIL_TAKEN" and r.json()["error"]["details"][
        "emails"
    ] == ["m3_be25@thapar.edu"]
    r = await anon.post(f"{API}/registration", json=_team("night  OWLS", n=2, start=10))
    assert r.json()["error"]["code"] == "NAME_TAKEN"


async def test_without_smtp_the_team_still_registers_and_organisers_resend(
    seeded, make_client, monkeypatch, smtp
):
    org, anon = make_client(), make_client()
    await _open(org)
    monkeypatch.setenv("SMTP_HOST", "")
    get_settings.cache_clear()
    r = await anon.post(f"{API}/registration", json=_team())
    assert r.json()["email_status"] == "not_configured" and smtp == []
    listed = (await org.get(f"{API}/admin/registrations", headers=AUTH)).json()
    assert listed["smtp_configured"] is False
    row = next(t for t in listed["teams"] if t["code"] == "T001")
    assert row["last_email"]["status"] == "not_configured" and len(row["members"]) == 3

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    get_settings.cache_clear()
    r = await org.post(f"{API}/admin/teams/T001/resend", headers=AUTH)
    assert r.json()["email_status"] == "sent" and len(smtp) == 1
    assert (await org.post(f"{API}/admin/teams/A01/resend", headers=AUTH)).json()["error"][
        "code"
    ] == "NO_MEMBERS"


async def test_a_mistaken_sign_up_can_be_removed(seeded, make_client, smtp):
    org, anon = make_client(), make_client()
    await _open(org)
    await anon.post(f"{API}/registration", json=_team())
    assert (await org.delete(f"{API}/admin/teams/T001", headers=AUTH)).json() == {"deleted": "T001"}
    # the addresses are free again
    assert (await anon.post(f"{API}/registration", json=_team())).status_code == 201


async def test_a_failing_mail_server_is_reported_not_fatal(seeded, make_client, monkeypatch, smtp):
    def boom(*a):
        raise OSError("connection refused")

    monkeypatch.setattr(mail, "_transport", boom)
    org, anon = make_client(), make_client()
    await _open(org)
    r = await anon.post(f"{API}/registration", json=_team())
    assert r.status_code == 201 and r.json()["email_status"] == "failed"
    row = (await org.get(f"{API}/admin/registrations", headers=AUTH)).json()["teams"]
    err = next(t for t in row if t["code"] == "T001")["last_email"]["error"]
    assert "connection refused" in err
