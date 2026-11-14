"""Which of the ELIGIBLE passages go into this turn's prompt? (the relevance half of retrieval)

Eligibility already decided what may be revealed; this only decides what's relevant.
Deterministic and explainable:
  score = 10 x (topics shared with the question) + 3 if cited in the last two turns
Passages that share no topic and weren't just discussed are left out entirely.
Highest score first (ties: higher mode, then id), until the token budget or passage cap.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.content.schema import Case, Mode

AWARE_TOKENS = 20  # an aware passage costs only its title + one line in the prompt


def passage_tokens(case: Case, passage_id: str, mode: Mode) -> int:
    return AWARE_TOKENS if mode == Mode.aware else case.passage[passage_id].approx_tokens


def rank_passages(
    case: Case,
    eligible: dict[str, Mode],
    question_topics: Iterable[str],
    recently_cited: Iterable[str] = (),
    *,
    budget_tokens: int,
    max_passages: int,
) -> list[str]:
    topics = set(question_topics)
    recent = set(recently_cited)
    scored = []
    for pid, mode in eligible.items():
        overlap = len(topics & set(case.passage[pid].topics))
        score = 10 * overlap + (3 if pid in recent else 0)
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
