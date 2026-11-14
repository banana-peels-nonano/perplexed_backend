"""Step 4: login, the per-team laptop cap, and company isolation on the first endpoints."""

import asyncio

API = "/api/v1"


async def _login(client, code, device="laptop"):
    return await client.post(
        f"{API}/auth/login", json={"access_code": code, "device_label": device}
    )


async def test_login_sets_cookie_and_me_returns_the_team(seeded, make_client):
    client = make_client()
    r = await _login(client, seeded["A01"])
    assert r.status_code == 200
    assert r.json() == {"code": "A01", "name": "Test team A01", "company_id": "A"}
    assert "pxd_session" in client.cookies
    assert "httponly" in r.headers["set-cookie"].lower()

    me = await client.get(f"{API}/me")
    assert me.status_code == 200
    assert me.json()["code"] == "A01"


async def test_login_accepts_messy_typing_and_rejects_wrong_codes(seeded, make_client):
    messy = seeded["A01"].lower().replace("-", " ")
    assert (await _login(make_client(), messy)).status_code == 200

    r = await _login(make_client(), "AAAA-BBBB-CCCC")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CODE"


async def test_no_cookie_or_logged_out_cookie_is_refused(seeded, make_client):
    assert (await make_client().get(f"{API}/stakeholders")).status_code == 401

    client = make_client()
    await _login(client, seeded["A01"])
    token = client.cookies["pxd_session"]
    assert (await client.post(f"{API}/auth/logout")).status_code == 204

    replay = make_client()  # someone reusing the old cookie after logout
    replay.cookies.set("pxd_session", token)
    r = await replay.get(f"{API}/stakeholders")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_two_laptops_per_team_even_when_logging_in_at_once(seeded, make_client):
    laptops = [make_client() for _ in range(3)]
    results = await asyncio.gather(
        *(_login(c, seeded["A01"], f"l{i}") for i, c in enumerate(laptops))
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 200, 409]  # the row lock makes the cap exact under concurrency

    winner = next(c for c, r in zip(laptops, results, strict=True) if r.status_code == 200)
    await winner.post(f"{API}/auth/logout")
    assert (await _login(make_client(), seeded["A01"])).status_code == 200  # a slot freed up


async def test_each_team_sees_only_its_own_company(seeded, make_client):
    a = make_client()
    await _login(a, seeded["A01"])
    listed = (await a.get(f"{API}/stakeholders")).json()
    assert [s["id"] for s in listed] == ["cfo", "ops_head", "qa_lead"]
    assert all(s["questions_remaining"] == 10 for s in listed)
    assert all("trust" not in s for s in listed)  # hidden by the rules

    b = make_client()
    await _login(b, seeded["B01"])
    assert [s["id"] for s in (await b.get(f"{API}/stakeholders")).json()] == ["kestrel_qc"]

    # a Kestrel team asking for Northfield's CFO gets the same answer as for a nonexistent id
    for sid in ("cfo", "ops_head", "qa_lead", "does_not_exist"):
        r = await b.get(f"{API}/stakeholders/{sid}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"
    assert (await a.get(f"{API}/stakeholders/cfo")).status_code == 200


async def test_healthz_checks_the_database(make_client, db):
    r = await make_client().get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
