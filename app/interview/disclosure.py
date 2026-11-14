"""Check a generated reply before anyone sees it, and build the evidence cards.

Pure function, no database and no model. Runs after generation, before persistence.

Violations (any one fails the reply; the caller regenerates once, then falls back):
  cited_not_provided   the model cited a passage that was not in its context
  cited_aware          it cited a passage it was only allowed to know exists
  figure_in_paraphrase the reply contains a fingerprint of a paraphrase-mode passage
  leaked_passage       the reply contains a fingerprint of a passage that was not provided
                       (same company only; another company's text should be unreachable)
  canary               the reply contains the stakeholder's canary (system prompt leak)
  ungrounded_fact      a figure, date, code or full name with no source (grounding.py)

Fingerprints that appear in `already_said` (this stakeholder's earlier answers to this
team) are not counted: those answers passed this check when they were given, so repeating
"you told me 15 percent" later reveals nothing new. The canary is always checked.

A passing reply also gets passages it clearly used but didn't cite added to its cards
(grounding.used_but_uncited), so the team's evidence board and unlock chains stay true.

Evidence cards carry the passage text from the case, never the model's wording, rendered
at the mode it was provided in (render.py): a paraphrase card shows no exact figures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.content.schema import Case, Mode
from app.interview.grounding import ungrounded_facts, used_but_uncited
from app.interview.render import render
from app.interview.text import mentions, normalise  # noqa: F401  (re-exported)


@dataclass(frozen=True)
class EvidenceCard:
    passage_id: str
    record_id: str
    record_title: str
    excerpt: str  # authentic text from the case/database


@dataclass
class DisclosureCheck:
    ok: bool
    violations: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    cards: list[EvidenceCard] = field(default_factory=list)
    auto_cited: list[str] = field(default_factory=list)  # used but not cited by the model


def check_reply(
    case: Case,
    stakeholder_id: str,
    provided: dict[str, Mode],
    cited: list[str],
    reply: str,
    already_said: str = "",
    question: str = "",
) -> DisclosureCheck:
    check = DisclosureCheck(ok=True)

    def fail(code: str, detail: str) -> None:
        if code not in check.violations:
            check.violations.append(code)
        check.details.append(detail)

    stakeholder = case.stakeholder[stakeholder_id]

    for pid in cited:
        if pid not in provided:
            fail("cited_not_provided", pid)
        elif provided[pid] == Mode.aware:
            fail("cited_aware", pid)

    for pid, mode in provided.items():
        if mode == Mode.paraphrase:
            for fp in case.passage[pid].fingerprints:
                if mentions(reply, fp) and not mentions(already_said, fp):
                    fail("figure_in_paraphrase", f"{pid}: {fp!r}")

    for pid, passage in case.passage.items():
        if pid in provided and provided[pid] >= Mode.paraphrase:
            continue
        if case.company_of_passage(pid) != stakeholder.company:
            continue
        for fp in passage.fingerprints:
            if mentions(reply, fp) and not mentions(already_said, fp):
                fail("leaked_passage", f"{pid}: {fp!r}")

    if mentions(reply, stakeholder.canary):
        fail("canary", stakeholder.canary)

    for fact in ungrounded_facts(case, stakeholder_id, provided, already_said, reply, question):
        fail("ungrounded_fact", fact)

    check.ok = not check.violations
    if check.ok:
        check.auto_cited = used_but_uncited(case, provided, cited, reply, question)
        for pid in dict.fromkeys([*cited, *check.auto_cited]):  # de-duplicate, keep order
            passage = case.passage[pid]
            record = case.record[passage.record]
            check.cards.append(
                EvidenceCard(
                    passage_id=pid,
                    record_id=passage.record,
                    record_title=record.title,
                    excerpt=render(case, pid, provided[pid]),
                )
            )
    return check
