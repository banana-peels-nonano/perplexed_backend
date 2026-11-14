"""Typed model of a case: companies, stakeholders, records, passages and access rules.

The same model validates the test fixture (tests/fixtures/*.toml) and, later, real
competition content (content/*.toml, git-ignored). Validation runs once at load time,
so a broken reference or a cross-company access rule fails loudly before the event,
not halfway through an interview.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class Tier(IntEnum):
    """Trust tiers, ordered: a higher tier never sees less than a lower one should."""

    guarded = 0
    neutral = 1
    open = 2


class Mode(IntEnum):
    """How much of a passage a stakeholder may use, ordered from least to most."""

    deny = 0  # never in context (also the default for unlisted passages)
    aware = 1  # title + one line only; may not be cited
    paraphrase = 2  # text in context; reply must not contain its fingerprints
    quote = 3  # text in context; may be quoted verbatim


ModeName = Literal["deny", "aware", "paraphrase", "quote"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Company(_Strict):
    name: str
    departments: list[str]
    topics: list[str]


class Stakeholder(_Strict):
    company: str
    department: str
    name: str
    role: str
    canary: str
    persona: str
    beliefs: list[str] = []


class Record(_Strict):
    company: str
    kind: str
    title: str
    date: str


class Passage(_Strict):
    record: str
    topics: list[str]
    text: str
    # what a paraphrase-mode stakeholder sees (and the card shows) instead of `text`;
    # required when the passage can be paraphrased and has a phrase fingerprint
    paraphrase: str | None = None
    fingerprints: list[str] = []
    deleted: bool = False
    # judge-only: never sent to a model or a participant
    truth: Literal["true", "misleading", "false"] = "true"
    clue: str | None = None

    @property
    def approx_tokens(self) -> int:
        # ~4 characters per token for English. Exact counts come from the provider later.
        return max(1, len(self.text) // 4)


class Access(_Strict):
    stakeholder: str
    passage: str
    guarded: ModeName
    neutral: ModeName
    open: ModeName
    # unlock rules: all present rules must pass
    labels: list[str] | None = None
    requires_topic: str | None = None
    requires_disclosed: list[str] = []
    phase_from: str | None = None

    def mode_at(self, tier: Tier) -> Mode:
        return Mode[getattr(self, tier.name)]

    @model_validator(mode="after")
    def _modes_never_shrink_as_trust_grows(self) -> Access:
        if not Mode[self.guarded] <= Mode[self.neutral] <= Mode[self.open]:
            raise ValueError(
                f"{self.stakeholder}/{self.passage}: modes must not decrease with trust "
                f"({self.guarded} -> {self.neutral} -> {self.open})"
            )
        return self


class Contradiction(_Strict):
    stakeholder: str
    claim: str
    refuted_by: list[str]


class Scenario(_Strict):
    name: str
    stakeholder: str
    tier: Literal["guarded", "neutral", "open"]
    label: str
    topics: list[str]
    phase: str
    disclosed: list[str] = []
    expect: dict[str, ModeName] = {}
    absent: list[str] = []


class CitationCase(_Strict):
    name: str
    stakeholder: str
    provided: dict[str, ModeName]
    cited: list[str]
    reply: str
    expect_ok: bool
    expect_violations: list[str] = []
    expect_cards: list[str] = []


class SmokeConversation(_Strict):
    stakeholder: str
    questions: list[str]


class Case(_Strict):
    schema_version: Literal[1]
    case_id: str
    phases: list[str]
    labels: list[str]
    company: dict[str, Company]
    stakeholder: dict[str, Stakeholder]
    record: dict[str, Record]
    passage: dict[str, Passage]
    access: list[Access]
    clue: dict[str, str] = {}
    contradiction: list[Contradiction] = []
    scenario: list[Scenario] = []
    citation_case: list[CitationCase] = []
    smoke_conversation: list[SmokeConversation] = []

    # ---- derived lookups (not part of the file)
    def company_of_passage(self, passage_id: str) -> str:
        return self.record[self.passage[passage_id].record].company

    def access_for(self, stakeholder_id: str) -> list[Access]:
        return [a for a in self.access if a.stakeholder == stakeholder_id]

    @model_validator(mode="after")
    def _cross_references(self) -> Case:
        errors: list[str] = []
        for sid, s in self.stakeholder.items():
            if s.company not in self.company:
                errors.append(f"stakeholder {sid}: unknown company {s.company}")
            elif s.department not in self.company[s.company].departments:
                errors.append(f"stakeholder {sid}: department {s.department} not in {s.company}")
        for rid, r in self.record.items():
            if r.company not in self.company:
                errors.append(f"record {rid}: unknown company {r.company}")
        for pid, p in self.passage.items():
            if p.record not in self.record:
                errors.append(f"passage {pid}: unknown record {p.record}")
                continue
            company = self.record[p.record].company
            vocab = set(self.company[company].topics) if company in self.company else set()
            for t in p.topics:
                if t not in vocab:
                    errors.append(f"passage {pid}: topic {t!r} not in company {company} vocabulary")
            if p.clue is not None and p.clue not in self.clue:
                errors.append(f"passage {pid}: unknown clue {p.clue}")
        seen: set[tuple[str, str]] = set()
        for a in self.access:
            key = (a.stakeholder, a.passage)
            if key in seen:
                errors.append(f"access {key}: duplicate entry")
            seen.add(key)
            if a.stakeholder not in self.stakeholder or a.passage not in self.passage:
                errors.append(f"access {key}: unknown stakeholder or passage")
                continue
            if self.company_of_passage(a.passage) != self.stakeholder[a.stakeholder].company:
                errors.append(f"access {key}: crosses company boundary")
            for dep in a.requires_disclosed:
                if dep not in self.passage:
                    errors.append(f"access {key}: requires unknown passage {dep}")
            if a.labels and not set(a.labels) <= set(self.labels):
                errors.append(f"access {key}: unknown labels {a.labels}")
            if a.phase_from and a.phase_from not in self.phases:
                errors.append(f"access {key}: unknown phase {a.phase_from}")
        errors += self._paraphrase_and_persona_errors()
        if errors:
            raise ValueError("invalid case:\n  " + "\n  ".join(errors))
        return self

    def _paraphrase_and_persona_errors(self) -> list[str]:
        """Authoring mistakes that would make the leak check fire on every answer (step 8)."""
        from app.interview.text import mentions  # pure text helpers; no app state

        errors = []
        paraphrased = {
            a.passage
            for a in self.access
            if a.passage in self.passage and any(a.mode_at(t) == Mode.paraphrase for t in Tier)
        }
        for pid, p in self.passage.items():
            for fp in p.fingerprints:
                if p.paraphrase and mentions(p.paraphrase, fp):
                    errors.append(f"passage {pid}: paraphrase contains its fingerprint {fp!r}")
            phrases = [f for f in p.fingerprints if not any(ch.isdigit() for ch in f)]
            if pid in paraphrased and phrases and not p.paraphrase:
                errors.append(
                    f"passage {pid}: can be paraphrased and has phrase fingerprints {phrases}; "
                    "write a `paraphrase` (figures are withheld automatically, phrases are not)"
                )
        for sid, s in self.stakeholder.items():
            own = " ".join([s.persona, *s.beliefs])
            for pid, p in self.passage.items():
                if pid in self.passage and self.company_of_passage(pid) == s.company:
                    for fp in p.fingerprints:
                        if mentions(own, fp):
                            errors.append(f"stakeholder {sid}: persona/beliefs contain {pid}'s "
                                          f"fingerprint {fp!r}")  # fmt: skip
        return errors
