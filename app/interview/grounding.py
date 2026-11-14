"""Does the reply state facts it had no source for, or use evidence it didn't cite?

Two deterministic checks on a generated reply, no model calls:

ungrounded_facts
    Every factual number (a figure, a date, a time, a code) and every full name in the
    reply must appear in what the stakeholder was given: this turn's evidence as she saw
    it (render.py), record titles, her persona and beliefs, her colleagues, and her own
    earlier answers to this team. The participant's questions are NOT a source, so
    "was the review on the 16th?" -> "yes, the 16th" fails unless she had the date.
    Step 7: she invented "Anil Rao" from "A. Rao", and confirmed a calendar entry she
    had no evidence of. Not caught: invented facts with no number or name in them.

used_but_uncited
    A provided passage she clearly drew on but left out of `cited` (three times in 30
    step-7 answers) gets cited for her, so the team gets its card and the disclosure is
    recorded (unlock chains read team_disclosure). Matching is on the passage's content
    words as she saw them, minus the words the participant just typed. A false match only
    shows a card she was allowed to quote or paraphrase this turn anyway.
"""

from __future__ import annotations

import re

from app.content.schema import Case, Mode
from app.interview.render import WITHHELD, render
from app.interview.text import normalise

# a number is a "fact" if it has a decimal/time/thousands part, is 13 or more, or carries
# a unit; "one of the reasons", "two quarters", "at ten" are not facts
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:[.:]\d+)*)(\s*(?:percent|crore|lakh|hours?)\b)?")
_HONORIFICS = {"Mr", "Mrs", "Ms", "Dr", "Miss", "Mx"}
_STOP = set(
    "the and for with from that this was were are has have had into onto over under than "
    "then they them their there what when which who whom why how our its it's not but "
    "all any per via about after before between during each more most some such only "
    "also very just been being will would could should may might can".split()
)


def _numbers(text: str) -> list[tuple[str, bool]]:
    """(number, is_fact) for every number in normalised text."""
    out = []
    for m in _NUMBER.finditer(normalise(text)):
        num, unit = m[1], m[2]
        fact = bool(unit) or bool(re.search(r"[.:]", num)) or int(num.split(".")[0]) >= 13
        out.append((num, fact))
    return out


def grounding_text(
    case: Case, stakeholder_id: str, provided: dict[str, Mode], already_said: str
) -> str:
    s = case.stakeholder[stakeholder_id]
    parts = [s.name, s.role, s.persona, *s.beliefs, already_said]
    parts += [c.name for c in case.company.values()]
    parts += [f"{c.name} {c.role}" for c in case.stakeholder.values() if c.company == s.company]
    for pid, mode in provided.items():
        parts.append(case.record[case.passage[pid].record].title)
        if mode >= Mode.paraphrase:
            parts.append(render(case, pid, mode))
    return "\n".join(parts)


def _known_people(case: Case) -> tuple[set[str], set[str]]:
    full = {c.name for c in case.stakeholder.values()}
    surnames = {n.split()[-1] for n in full}
    for p in case.passage.values():
        surnames.update(re.findall(r"\b[A-Z]\. ([A-Z][a-z]+)", p.text))
    return full, surnames


def ungrounded_facts(
    case: Case, stakeholder_id: str, provided: dict[str, Mode], already_said: str, reply: str
) -> list[str]:
    source = grounding_text(case, stakeholder_id, provided, already_said)
    known = {n for n, _ in _numbers(source)}
    found = [n for n, fact in _numbers(reply) if fact and n not in known]

    full, surnames = _known_people(case)
    for first, last in re.findall(r"\b([A-Z][a-z]+) ([A-Z][a-z]+)\b", reply):
        if last in surnames and first not in _HONORIFICS:
            name = f"{first} {last}"
            if name not in full or name not in source:
                found.append(name)  # invented ("Anil Rao") or someone she can't know
    return list(dict.fromkeys(found))


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+(?:[.:][0-9]+)*", text.casefold().replace("'s", ""))
    return {w for w in words if len(w) >= 3 and w not in _STOP and w != WITHHELD.strip("[]")}


def used_but_uncited(
    case: Case,
    provided: dict[str, Mode],
    cited: list[str],
    reply: str,
    question: str,
    *,
    min_words: int = 3,
    min_share: float = 0.35,
) -> list[str]:
    said = _content_words(reply)
    asked = _content_words(question)
    used = []
    for pid, mode in provided.items():
        if mode < Mode.paraphrase or pid in cited:
            continue
        words = _content_words(render(case, pid, mode)) - asked
        hits = words & said
        if words and len(hits) >= min_words and len(hits) / len(words) >= min_share:
            used.append(pid)
    return used
