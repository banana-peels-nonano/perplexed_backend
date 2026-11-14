"""The team's evidence board: what it may contain, and how it is checked.

Deliberately generic until the frontend design is settled: cards (evidence, quote,
note), links between them with a team-chosen label, and a size-capped `extra` object
for whatever else the UI needs (hypotheses, view settings). The server checks only what
protects the game:

  evidence  a passage the team has actually been shown (team_disclosure)
  quote     a span of one of the team's own *answered* messages; the server cuts the
            text itself, so a quote can't be edited into something nobody said
  note      the team's own words, length-capped
  links     between cards on this board, with a short label ("contradicts", ...)

Links are drawn by the team, never computed by the server: a computed link would tell
teams which pieces of evidence belong together, which is the work being judged.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CARDS = 300
MAX_LINKS = 600
MAX_NOTE = 1000
MAX_LABEL = 40
MAX_EXTRA_BYTES = 32 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Card(_Strict):
    id: str
    kind: Literal["evidence", "quote", "note"]
    passage_id: str | None = None  # evidence
    message_id: str | None = None  # quote
    start: int | None = Field(default=None, ge=0)  # quote: character offsets into the answer
    end: int | None = Field(default=None, ge=0)
    text: str | None = None  # note: the team's words; quote: filled in by the server
    stakeholder_id: str | None = None  # quote: filled in by the server
    x: float | None = None
    y: float | None = None

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("card id: 1-64 letters, digits, '_' or '-'")
        return v

    @model_validator(mode="after")
    def _fields_for_kind(self) -> Card:
        if self.kind == "evidence" and not self.passage_id:
            raise ValueError(f"card {self.id}: evidence needs passage_id")
        if self.kind == "quote":
            if not self.message_id or self.start is None or self.end is None:
                raise ValueError(f"card {self.id}: quote needs message_id, start and end")
            if self.end <= self.start:
                raise ValueError(f"card {self.id}: quote end must be after start")
        if self.kind == "note" and not (self.text and self.text.strip()):
            raise ValueError(f"card {self.id}: note needs text")
        if self.kind == "note" and len(self.text or "") > MAX_NOTE:
            raise ValueError(f"card {self.id}: note longer than {MAX_NOTE} characters")
        return self


class Link(_Strict):
    from_: str = Field(alias="from")
    to: str
    label: str = Field(default="", max_length=MAX_LABEL)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class BoardIn(_Strict):
    version: int = Field(ge=0)  # the version this edit is based on (0 = empty board)
    cards: list[Card] = Field(default_factory=list, max_length=MAX_CARDS)
    links: list[Link] = Field(default_factory=list, max_length=MAX_LINKS)
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> BoardIn:
        ids = [c.id for c in self.cards]
        if len(ids) != len(set(ids)):
            raise ValueError("card ids must be unique")
        known = set(ids)
        for link in self.links:
            if link.from_ not in known or link.to not in known:
                raise ValueError(f"link {link.from_} -> {link.to}: both ends must be cards")
            if link.from_ == link.to:
                raise ValueError(f"link {link.from_}: a card can't link to itself")
        if len(json.dumps(self.extra)) > MAX_EXTRA_BYTES:
            raise ValueError(f"extra is larger than {MAX_EXTRA_BYTES} bytes")
        return self


def stored(board: BoardIn) -> dict[str, Any]:
    """The JSON kept in team_board.doc (version lives in its own column)."""
    return {
        "cards": [c.model_dump(exclude_none=True) for c in board.cards],
        "links": [link.model_dump(by_alias=True) for link in board.links],
        "extra": board.extra,
    }
