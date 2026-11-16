"""Step 12: live notifications. What changed is announced; the data never travels on it."""

import asyncio
import uuid

import pytest
from fastapi import WebSocketDisconnect

from app.api.live import serve
from app.config import get_settings
from app.db import make_sessionmaker
from app.live.hub import MAX_PER_TEAM, Connection, Hub

API = "/api/v1"
TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
QA = {"label": "specific", "topics": ["qa_budget"], "respectful": True}


class FakeSocket:
    """Enough of Starlette's WebSocket for the hub and the handler."""

    def __init__(self, cookies=None, headers=None, incoming=0, fail=False):
        self.cookies = cookies or {}
        self.headers = {"host": "portal.test", **(headers or {})}
        self.sent: list[dict] = []
        self.closed: tuple | None = None
        self.accepted = False
        self._incoming = incoming  # messages the client sends before it disconnects
        self._fail = fail

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        if self._fail:
            raise RuntimeError("socket gone")
        self.sent.append(data)

    async def close(self, code=1000, reason=None):
        self.closed = (code, reason)

    async def receive_text(self):
        if self._incoming <= 0:
            raise WebSocketDisconnect(1000)
        self._incoming -= 1
        await asyncio.sleep(0.02)
        return "hi"


# ------------------------------------------------------------------ the hub
async def test_hub_sends_to_the_right_team_and_forgets_dead_sockets():
    hub = Hub()
    a, b = uuid.uuid4(), uuid.uuid4()
    a1, a2, b1, dead = FakeSocket(), FakeSocket(), FakeSocket(), FakeSocket(fail=True)
    for sock, team in [(a1, a), (a2, a), (b1, b), (dead, a)]:
        assert hub.add(Connection(sock, team, uuid.uuid4()))
    await hub.publish(a, {"type": "evidence"})
    assert a1.sent == a2.sent == [{"type": "evidence"}] and b1.sent == []
    assert hub.count(a) == 2  # the dead one was dropped on the failed send
    await hub.broadcast({"type": "phase"})
    assert b1.sent == [{"type": "phase"}] and a1.sent[-1] == {"type": "phase"}


async def test_hub_caps_tabs_per_team_and_logout_closes_only_that_laptop():
    hub = Hub()
    team, laptop1, laptop2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    socks = [FakeSocket() for _ in range(MAX_PER_TEAM)]
    for i, sock in enumerate(socks):
        assert hub.add(Connection(sock, team, laptop1 if i % 2 else laptop2))
    assert not hub.add(Connection(FakeSocket(), team, laptop1))  # one tab too many
    await hub.close_session(laptop1)
    assert hub.count(team) == MAX_PER_TEAM // 2
    assert all(s.closed == (4401, "logged out") for i, s in enumerate(socks) if i % 2)
    assert all(s.closed is None for i, s in enumerate(socks) if not i % 2)


# ------------------------------------------------------------------ the handshake
async def _login_token(make_client, seeded):
    client = make_client()
    r = await client.post(f"{API}/auth/login", json={"access_code": seeded["A01"]})
    return client, r.cookies["pxd_session"]


async def test_socket_needs_a_valid_session_and_our_origin(seeded, make_client, db):
    maker, hub = make_sessionmaker(db), Hub()
    nobody = FakeSocket()
    await serve(nobody, maker, hub)
    assert nobody.closed[0] == 4401 and hub.count() == 0

    _, token = await _login_token(make_client, seeded)
    stranger = FakeSocket({"pxd_session": token}, {"origin": "https://evil.example"})
    await serve(stranger, maker, hub)
    assert stranger.closed[0] == 4403

    tab = FakeSocket({"pxd_session": token}, {"origin": "https://portal.test"}, incoming=1)
    await serve(tab, maker, hub, ping_seconds=0.01)  # returns when the tab disconnects
    assert tab.sent[0] == {"type": "hello", "team": "A01"}
    assert {"type": "ping"} in tab.sent  # kept alive while idle
    assert hub.count() == 0  # cleaned up on disconnect


async def test_a_logged_out_session_cannot_open_a_socket(seeded, make_client, db):
    client, token = await _login_token(make_client, seeded)
    await client.post(f"{API}/auth/logout")
    sock = FakeSocket({"pxd_session": token})
    await serve(sock, make_sessionmaker(db), Hub())
    assert sock.closed[0] == 4401


# ------------------------------------------------------------------ what gets announced
@pytest.fixture
def announced(monkeypatch):
    sent: list[tuple] = []

    async def publish(self, team_id, *events):
        sent.extend(("team", team_id, e) for e in events)

    async def broadcast(self, event):
        sent.append(("all", None, event))

    monkeypatch.setattr(Hub, "publish", publish)
    monkeypatch.setattr(Hub, "broadcast", broadcast)
    return sent


async def test_asking_and_saving_announce_to_the_team(seeded, make_client, fake, announced):
    client, _ = await _login_token(make_client, seeded)
    fake.queues["classify"].append(QA)
    fake.queues["answer"].append(
        {"reply": "We trimmed QA spend to protect our margin.", "cited": ["A-FIN-002#1"]}
    )
    r = await client.post(
        f"{API}/stakeholders/cfo/questions",
        json={"client_request_id": str(uuid.uuid4()), "text": "What happened to QA spend?"},
    )
    assert r.status_code == 200
    events = [e for scope, _, e in announced]
    assert {"type": "thread", "stakeholder_id": "cfo"} in events
    assert {"type": "evidence"} in events and {"type": "stakeholders"} in events
    assert all(scope == "team" for scope, _, _ in announced)
    # nothing secret rides on the socket: no answer text, no excerpts
    assert "margin" not in str(announced)

    announced.clear()
    note = {"id": "n1", "kind": "note", "text": "check sampling"}
    await client.put(f"{API}/board", json={"version": 0, "cards": [note]})
    assert announced == [("team", announced[0][1], {"type": "board", "version": 1})]


async def test_organiser_actions_announce_to_everyone(seeded, make_client, announced, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    get_settings.cache_clear()
    org = make_client()
    await org.post(f"{API}/admin/phases/current/pause", headers=AUTH)
    await org.post(f"{API}/admin/quota", json={"quota": 12}, headers=AUTH)
    await org.post(
        f"{API}/admin/content/reload",
        json={"path": "tests/fixtures/northfield.toml"},
        headers=AUTH,
    )
    assert [e for _, _, e in announced] == [
        {"type": "phase"},
        {"type": "stakeholders"},
        {"type": "content"},
    ]
    assert (await org.get(f"{API}/admin/live", headers=AUTH)).json() == {"connections": 0}
    get_settings.cache_clear()
