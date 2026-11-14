"""Check a generated reply before anyone sees it, and build the evidence cards.

Pure function, no database and no model. Runs after generation, before persistence.

Violations (any one fails the reply; the caller regenerates once, then falls back):
  cited_not_provided   the model cited a passage that was not in its context
  cited_aware          it cited a passage it was only allowed to know exists
  figure_in_paraphrase the reply contains a fingerprint of a paraphrase-mode passage
  leaked_passage       the reply contains a fingerprint of a passage that was not provided
                       (same company only; another company's text should be unreachable)
  canary               the reply contains the stakeholder's canary (system prompt leak)

Fingerprints that appear in `already_said` (this stakeholder's earlier answers to this
team) are not counted: those answers passed this check when they were given, so repeating
"you told me 15 percent" later reveals nothing new. The canary is always checked.

Evidence cards carry the passage text from the case, never the model's wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.content.schema import Case, Mode


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


_UNITS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_U = "|".join(_UNITS[1:10])
_T = "|".join(_TENS)


def normalise(text: str) -> str:
    """The form fingerprints are compared in: case, spelling and punctuation flattened.

    Step 7: haiku wrote "nine percent operating margin" for the fingerprint
    "9 percent operating margin", and "fifteen percent" for "15 percent". Matching the
    literal string missed both. Number words up to ninety-nine become digits, and the
    common spellings of percent, crore, thousands separators, hyphens and curly quotes
    are made the same, so the fingerprint and the reply meet in one form.
    """
    t = text.casefold().replace("\u2019", "'").replace("\u2018", "'")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 3,050 -> 3050
    t = re.sub(r"(\w)-(\w)", r"\1 \2", t)  # twenty-five, 1-in-20
    t = re.sub(
        rf"\b({_T})\s+({_U})\b",
        lambda m: str(10 * (_TENS.index(m[1]) + 2) + _UNITS.index(m[2])),
        t,
    )
    t = re.sub(rf"\b({_T})\b", lambda m: str(10 * (_TENS.index(m[1]) + 2)), t)
    t = re.sub(r"\b(" + "|".join(_UNITS) + r")\b", lambda m: str(_UNITS.index(m[1])), t)
    t = re.sub(r"\s*%", " percent", t)
    t = re.sub(r"\bper\s+cent\b|\bpct\b", "percent", t)
    t = re.sub(r"\b(crores|cr)\b\.?", "crore", t)
    return re.sub(r"\s+", " ", t)


def mentions(text: str, fingerprint: str) -> bool:
    """True if `text` contains `fingerprint` as whole words, after both are normalised.

    Whole words, so "19 percent" or "1.15 percent" never count as "9 percent"/"15 percent".
    """
    fp = re.escape(normalise(fingerprint).strip())
    return re.search(rf"(?<![\w.]){fp}(?!\w)", normalise(text)) is not None


def check_reply(
    case: Case,
    stakeholder_id: str,
    provided: dict[str, Mode],
    cited: list[str],
    reply: str,
    already_said: str = "",
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

    check.ok = not check.violations
    if check.ok:
        for pid in dict.fromkeys(cited):  # de-duplicate, keep order
            passage = case.passage[pid]
            record = case.record[passage.record]
            check.cards.append(
                EvidenceCard(
                    passage_id=pid,
                    record_id=passage.record,
                    record_title=record.title,
                    excerpt=passage.text,
                )
            )
    return check
