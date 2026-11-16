"""Import every model here so Base.metadata knows all tables (Alembic reads it)."""

from app.models.base import Base
from app.models.content import (
    Clue,
    Company,
    Department,
    Passage,
    Record,
    Stakeholder,
    StakeholderPassageAccess,
)
from app.models.runtime import (
    Announcement,
    Event,
    HrFlag,
    LlmCall,
    Message,
    Phase,
    Team,
    TeamBoard,
    TeamDepartmentState,
    TeamDisclosure,
    TeamSession,
    TeamStakeholderState,
    TeamTimeBonus,
)

__all__ = [
    "Announcement",
    "Base",
    "Clue",
    "Company",
    "Department",
    "Event",
    "HrFlag",
    "LlmCall",
    "Message",
    "Passage",
    "Phase",
    "Record",
    "Stakeholder",
    "StakeholderPassageAccess",
    "Team",
    "TeamBoard",
    "TeamDepartmentState",
    "TeamDisclosure",
    "TeamSession",
    "TeamStakeholderState",
    "TeamTimeBonus",
]
