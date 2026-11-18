"""The records tab: a company's internal documents, shown on the portal when organisers allow.

Three modes, set live from the console (event config `records_tab.mode`):

  off     (default) records come out only through interviews
  titles  a catalogue: each document's title, kind and date, no text. Teams know what to ask for.
  full    the documents themselves. Every passage on the tab becomes evidence the team may
          pin and cite, like interview evidence; after an alliance locks, the partner
          company's documents too.

Never on the tab: "interview" records (what a stakeholder says only in confidence) and
deleted passages (text removed from a document; only people remember it).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.content.store import current_event
from app.models import Passage, Record, Team

MODES = ("off", "titles", "full")
HIDDEN_KINDS = ("interview",)


def tab_mode(config: dict) -> str:
    mode = (config.get("records_tab") or {}).get("mode", "off")
    return mode if mode in MODES else "off"


def tab_records_query(companies):
    return (
        select(Record)
        .where(Record.company_id.in_(companies), Record.kind.not_in(HIDDEN_KINDS))
        .order_by(Record.company_id, Record.record_date.nulls_last(), Record.id)
    )


async def tab_passage_ids(session, companies) -> set[str]:
    if not companies:
        return set()
    return set(
        await session.scalars(
            select(Passage.id)
            .join(Record, Record.id == Passage.record_id)
            .where(
                Record.company_id.in_(companies),
                Record.kind.not_in(HIDDEN_KINDS),
                Passage.deleted.is_(False),
            )
        )
    )


async def tab_companies(session, team_id: uuid.UUID, partners: list[uuid.UUID]) -> list[str]:
    """The team's company first, then its locked partners' companies."""
    own = await session.scalar(select(Team.company_id).where(Team.id == team_id))
    out = [own] if own else []
    if partners:
        for c in await session.scalars(select(Team.company_id).where(Team.id.in_(partners))):
            if c and c not in out:
                out.append(c)
    return out


async def full_tab_passages(session, team_id: uuid.UUID, partners: list[uuid.UUID]) -> set[str]:
    """Passages the records tab makes usable for this team: none unless the mode is full."""
    if tab_mode((await current_event(session)).config) != "full":
        return set()
    return await tab_passage_ids(session, await tab_companies(session, team_id, partners))
