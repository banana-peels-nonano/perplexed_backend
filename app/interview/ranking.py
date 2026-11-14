"""Which of the ELIGIBLE passages go into this turn's prompt? (the relevance half of retrieval)

Eligibility already decided what may be revealed; this only decides what's relevant.
Deterministic and explainable:
  question has topics:  score = 10 x (topics shared with the question), +3 if also cited
                        in the last two turns (a tie-break, never a ticket in)
  question has none:    score = 3 if cited in the last two turns ("say more about that?")
  then, for passages already in (score > 0): +2 per content word shared with the
  question, up to +8 (full-text, so "sampling ... April" finds the April sampling memo)
Passages with score 0 are left out entirely.

Step 7, 10-question run: the old rule (+3 always) carried every earlier passage into every
later question, and at open trust the CFO re-told the cancelled staffing review in four
answers running, whatever was asked.
Highest score first (ties: higher mode, then id), until the token budget or passage cap.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.content.schema import Case, Mode
from app.interview.render import render

AWARE_TOKENS = 20  # an aware passage costs only its title + one line in the prompt


def passage_tokens(case: Case, passage_id: str, mode: Mode) -> int:
    return AWARE_TOKENS if mode == Mode.aware else case.passage[passage_id].approx_tokens


_STOP = set(
    "the and for with from that this was were are has have had what when which who why how "
    "our its not but all any about after before did does was you your there their they "
    "been being will would could should can exactly".split()
)


def _words(text: str) -> set[str]:
    """Content words, lightly stemmed (plural s), for the full-text part of the score."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.casefold()):
        if len(w) < 3 or w in _STOP:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


def _searchable(case: Case, pid: str, mode: Mode) -> str:
    """What the stakeholder would see: never search text she isn't shown (aware = title)."""
    record = case.record[case.passage[pid].record]
    return record.title if mode == Mode.aware else f"{record.title} {render(case, pid, mode)}"


def rank_passages(
    case: Case,
    eligible: dict[str, Mode],
    question_topics: Iterable[str],
    recently_cited: Iterable[str] = (),
    *,
    budget_tokens: int,
    max_passages: int,
    question: str = "",
) -> list[str]:
    topics = set(question_topics)
    recent = set(recently_cited)
    asked = _words(question)
    scored = []
    for pid, mode in eligible.items():
        overlap = len(topics & set(case.passage[pid].topics))
        if topics:
            score = 10 * overlap + (3 if overlap and pid in recent else 0)
        else:
            score = 3 if pid in recent else 0
        if score > 0 and asked:
            # full-text part (D-017 specified "topics + full-text"; step 8 recall misses were
            # both topic ties broken by id): +2 per shared content word, at most +8, so it
            # orders passages within a topic but never outranks a better topic match
            score += 2 * min(4, len(asked & _words(_searchable(case, pid, mode))))
        if score > 0:
            scored.append((-score, -int(mode), pid))
    chosen: list[str] = []
    used = 0
    for _, neg_mode, pid in sorted(scored):
        cost = passage_tokens(case, pid, Mode(-neg_mode))
        if used + cost > budget_tokens:
            continue  # a smaller, lower-ranked passage may still fit
        chosen.append(pid)
        used += cost
        if len(chosen) == max_passages:
            break
    return chosen
