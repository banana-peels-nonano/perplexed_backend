"""Import every model here so Base.metadata knows all tables (Alembic reads it)."""

from app.models.base import Base
from app.models.content import (
    Company,
    Department,
    Passage,
    Record,
    Stakeholder,
    StakeholderPassageAccess,
)
from app.models.runtime import (
    Event,
    LlmCall,
    Message,
    Phase,
    Team,
    TeamDisclosure,
    TeamSession,
    TeamStakeholderState,
)

__all__ = [
    "Base",
    "Company",
    "Department",
    "Event",
    "LlmCall",
    "Message",
    "Passage",
    "Phase",
    "Record",
    "Stakeholder",
    "StakeholderPassageAccess",
    "Team",
    "TeamDisclosure",
    "TeamSession",
    "TeamStakeholderState",
]
