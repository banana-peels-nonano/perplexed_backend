"""Who a team's alliance partners are, and what evidence the team may use because of them.

Partner evidence is shared only once an alliance is locked (every member confirmed, or
organisers paired them), and only inside that alliance.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import AllianceMember, TeamDisclosure


async def partner_team_ids(session, team_id: uuid.UUID) -> list[uuid.UUID]:
    mine = select(AllianceMember.alliance_id).where(
        AllianceMember.team_id == team_id, AllianceMember.locked
    )
    return list(
        await session.scalars(
            select(AllianceMember.team_id).where(
                AllianceMember.alliance_id.in_(mine), AllianceMember.team_id != team_id
            )
        )
    )


async def usable_passages(session, team_id: uuid.UUID) -> set[str]:
    """Passages the team may cite or pin: its own, plus its locked partners', plus whatever
    the records tab shows when organisers have set it to full (app/portal/records.py)."""
    from app.portal.records import full_tab_passages  # records imports the content store

    partners = await partner_team_ids(session, team_id)
    shown = set(
        await session.scalars(
            select(TeamDisclosure.passage_id).where(
                TeamDisclosure.team_id.in_([team_id, *partners])
            )
        )
    )
    return shown | await full_tab_passages(session, team_id, partners)
