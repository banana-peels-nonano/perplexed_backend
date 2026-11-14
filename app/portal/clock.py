"""Phase time left, from the stored timestamps. Pure: no database, `now` passed in."""

from __future__ import annotations

from datetime import datetime


def remaining_seconds(
    *,
    status: str,
    planned_seconds: int,
    extended_seconds: int,
    total_paused_seconds: int,
    started_at: datetime | None,
    paused_at: datetime | None,
    now: datetime,
) -> int:
    """Seconds left in a phase. A paused phase's clock is frozen at the moment of pausing.

    budget  = planned + extended
    elapsed = (now, or paused_at while paused) - started_at - time spent paused before
    """
    budget = planned_seconds + extended_seconds
    if status == "ended":
        return 0
    if status == "scheduled" or started_at is None:
        return budget
    until = paused_at if status == "paused" and paused_at is not None else now
    elapsed = (until - started_at).total_seconds() - total_paused_seconds
    return max(0, int(budget - elapsed))
