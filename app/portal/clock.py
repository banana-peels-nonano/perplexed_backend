"""Phase time left, from the stored timestamps.

The arithmetic is pure (no database, `now` passed in); team_bonus() at the bottom is the
one lookup: the seconds organisers gave one team back after a crash.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.models import TeamRoundStatus, TeamTimeBonus


def remaining_seconds(
    *,
    status: str,
    planned_seconds: int,
    extended_seconds: int,
    total_paused_seconds: int,
    started_at: datetime | None,
    paused_at: datetime | None,
    now: datetime,
) -> int | None:
    """Seconds left in a phase, or None if it has no timer (planned_seconds == 0).
    A paused phase's clock is frozen at the moment of pausing.

    budget  = planned + extended
    elapsed = (now, or paused_at while paused) - started_at - time spent paused before
    """
    if planned_seconds == 0:
        return None  # untimed (e.g. 'submission_closed'): ends only when organisers end it
    budget = planned_seconds + extended_seconds
    if status == "ended":
        return 0
    if status == "scheduled" or started_at is None:
        return budget
    until = paused_at if status == "paused" and paused_at is not None else now
    elapsed = (until - started_at).total_seconds() - total_paused_seconds
    return max(0, int(budget - elapsed))


def phase_remaining(phase, now: datetime, bonus: int = 0) -> int | None:
    """remaining_seconds() for a Phase row. `bonus`: extra seconds one team was given back
    (team_time_bonus); it moves only that team's clock. Ended stays ended."""
    return remaining_seconds(
        status=phase.status,
        planned_seconds=phase.planned_seconds,
        extended_seconds=phase.extended_seconds + bonus,
        total_paused_seconds=phase.total_paused_seconds,
        started_at=phase.started_at,
        paused_at=phase.paused_at,
        now=now,
    )


def interviews_open(phase, now: datetime, bonus: int = 0) -> bool:
    """Running, allows interviews, and (if timed) not out of time for this team."""
    if phase is None or phase.status != "running" or not phase.capabilities.get("interview"):
        return False
    left = phase_remaining(phase, now, bonus)
    return left is None or left > 0


async def team_bonus(session, team_id, phase) -> int:
    """Seconds this team was given back in this phase (0 if none, or no phase)."""
    if phase is None:
        return 0
    seconds = await session.scalar(
        select(TeamTimeBonus.seconds).where(
            TeamTimeBonus.team_id == team_id, TeamTimeBonus.phase_id == phase.id
        )
    )
    return seconds or 0


async def not_in_round(session, team_id, phase) -> bool:
    """True when organisers have published that this team is out of this round."""
    if phase is None:
        return False
    status = await session.scalar(
        select(TeamRoundStatus.status).where(
            TeamRoundStatus.team_id == team_id,
            TeamRoundStatus.phase_id == phase.id,
            TeamRoundStatus.published_at.is_not(None),
        )
    )
    return status == "eliminated"
