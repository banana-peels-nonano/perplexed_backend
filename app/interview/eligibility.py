"""Which passages may this stakeholder use with this team, right now?

Pure function, no database and no model: the authorisation half of retrieval.
Relevance ranking (which of the eligible passages answer the question) is a separate step.

A passage is eligible only if ALL of these hold:
  - it belongs to the stakeholder's company (re-checked here, not trusted from content),
  - the stakeholder has an access entry for it whose mode at the current tier is not deny,
  - every unlock rule on that entry passes: question label, required topic,
    passages the team must already have been shown, and earliest phase.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.content.schema import Case, Mode, Tier


@dataclass(frozen=True)
class QuestionContext:
    stakeholder: str
    tier: Tier
    label: str  # from the classifier
    topics: frozenset[str]  # from the classifier, within the company vocabulary
    phase: str
    disclosed: frozenset[str]  # passage ids this TEAM has already been shown, any stakeholder
    # the event's rounds in running order (organisers can add rounds the case file never
    # named); None = the order in the case file
    phase_order: tuple[str, ...] | None = None
    # records whose handout exists but hasn't reached this team: capped at aware (title only)
    withheld_records: frozenset[str] = frozenset()


def eligible_passages(case: Case, q: QuestionContext) -> dict[str, Mode]:
    """Return {passage_id: mode} for every passage the stakeholder may use. Never includes deny."""
    stakeholder = case.stakeholder[q.stakeholder]
    order = list(q.phase_order or case.phases)
    if q.phase not in order:
        order.append(q.phase)
    phase_index = order.index(q.phase)
    result: dict[str, Mode] = {}
    for entry in case.access_for(q.stakeholder):
        if case.company_of_passage(entry.passage) != stakeholder.company:
            continue  # defence in depth; the loader already rejects this
        mode = entry.mode_at(q.tier)
        if mode == Mode.deny:
            continue
        if entry.labels is not None and q.label not in entry.labels:
            continue
        if entry.requires_topic is not None and entry.requires_topic not in q.topics:
            continue
        if not set(entry.requires_disclosed) <= q.disclosed:
            continue
        if entry.phase_from is not None and (
            entry.phase_from not in order  # that round was deleted: it never arrives
            or phase_index < order.index(entry.phase_from)
        ):
            continue
        if case.passage[entry.passage].record in q.withheld_records:
            mode = min(mode, Mode.aware)
        result[entry.passage] = mode
    return result


def question_context(
    *,
    stakeholder: str,
    tier: str | Tier,
    label: str,
    topics: Iterable[str],
    phase: str,
    disclosed: Iterable[str] = (),
    phase_order: Iterable[str] | None = None,
    withheld_records: Iterable[str] = (),
) -> QuestionContext:
    return QuestionContext(
        stakeholder=stakeholder,
        tier=tier if isinstance(tier, Tier) else Tier[tier],
        label=label,
        topics=frozenset(topics),
        phase=phase,
        disclosed=frozenset(disclosed),
        phase_order=tuple(phase_order) if phase_order is not None else None,
        withheld_records=frozenset(withheld_records),
    )
