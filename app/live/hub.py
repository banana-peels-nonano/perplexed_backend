"""Live notifications: "something changed, refetch it". Never the data itself.

One WebSocket per open tab. When something a team can see changes, the server sends a
tiny event naming *what* changed; the tab then refetches it through the normal HTTP
endpoints, which apply every rule (company isolation, disclosure, rendering). The socket
never carries content, so it can never become a second way for secrets to leave.

    {"type": "phase"}                              every team: phase or clock changed
    {"type": "content"}                            every team: case content reloaded
    {"type": "stakeholders"}                       questions left / availability changed
    {"type": "thread", "stakeholder_id": "cfo"}    a thread got a new message
    {"type": "evidence"}                           the team found new evidence
    {"type": "board", "version": 4}                someone saved the board
    {"type": "team"}                               the team's company changed: refetch
                                                   /status and /brief
    {"type": "announcement"}                       organisers broadcast (or retracted)
                                                   something: refetch /announcements
    {"type": "documents"}                          refetch /documents
    {"type": "rounds"}                             a round result was published: /rounds
    {"type": "alliance"}                           /alliance and /alliance/evidence
    {"type": "submission"}                         a teammate saved the sheet: /submission
    {"type": "ping"}                               keep-alive, every 25 s; ignore it

The registry lives in this process's memory (no Redis, D-009), so the app must run as a
single process: `uvicorn app.main:app` without `--workers`. Phones and laptops that drop
the connection simply reconnect and refetch.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.observability import event as log_event

SEND_TIMEOUT = 2.0  # a stuck client must not hold up everyone else's notifications
MAX_PER_TEAM = 8  # tabs across the team's (at most 2) logged-in laptops


class Socket(Protocol):
    async def send_json(self, data: Any) -> None: ...
    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


@dataclass(eq=False)
class Connection:
    socket: Socket
    team_id: uuid.UUID
    session_id: uuid.UUID


@dataclass
class Hub:
    _teams: dict[uuid.UUID, set[Connection]] = field(default_factory=dict)

    def add(self, conn: Connection) -> bool:
        conns = self._teams.setdefault(conn.team_id, set())
        if len(conns) >= MAX_PER_TEAM:
            return False
        conns.add(conn)
        return True

    def remove(self, conn: Connection) -> None:
        conns = self._teams.get(conn.team_id)
        if conns is not None:
            conns.discard(conn)
            if not conns:
                del self._teams[conn.team_id]

    def count(self, team_id: uuid.UUID | None = None) -> int:
        if team_id is not None:
            return len(self._teams.get(team_id, ()))
        return sum(len(c) for c in self._teams.values())

    async def _send(self, conns: list[Connection], event: dict[str, Any]) -> None:
        async def one(conn: Connection) -> bool:
            try:
                await asyncio.wait_for(conn.socket.send_json(event), SEND_TIMEOUT)
            except Exception:  # gone or stuck: forget it; the tab will reconnect
                self.remove(conn)
                return False
            return True

        if conns:
            sent = await asyncio.gather(*(one(c) for c in conns))
            if failed := sent.count(False):
                log_event(
                    "ws.delivery_failed",
                    level=logging.WARNING,
                    limit=60,
                    notification=event.get("type"),
                    failed=failed,
                    attempted=len(conns),
                )

    async def publish(self, team_id: uuid.UUID, *events: dict[str, Any]) -> None:
        """Events for one team's tabs only."""
        conns = list(self._teams.get(team_id, ()))
        for event in events:
            await self._send(conns, event)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """An event for every connected tab (phase, content)."""
        await self._send([c for conns in self._teams.values() for c in conns], event)

    async def close_session(self, session_id: uuid.UUID) -> None:
        """Logout: that laptop's tabs are disconnected; the team's other laptop stays."""
        for conn in [c for conns in self._teams.values() for c in conns]:
            if conn.session_id == session_id:
                self.remove(conn)
                try:
                    await conn.socket.close(code=4401, reason="logged out")
                except Exception:
                    pass
