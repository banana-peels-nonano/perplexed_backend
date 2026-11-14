"""One question, end to end.

    T1 (short transaction)   check phase + company, reserve the question atomically,
                             write the pending question, read history and disclosures
    no connection held       classify -> eligible -> ranked -> prompt -> generate -> check
                             (regenerate once on a violation, then a safe deflection)
    T2 (short transaction)   save the answer, newly discovered evidence, model-call costs,
                             clear the in-flight flag
    on model failure         refund: delete the pending question, give the quota back

No database connection is held while the model is thinking (D-005): a 3-5 s call holding
one of 20 pooled connections would stall the whole app at ~20 concurrent questions.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import ApiError
from app.auth.context import TeamContext
from app.content.schema import Case, Mode, Tier
from app.content.store import current_event
from app.interview.disclosure import check_reply
from app.interview.eligibility import eligible_passages, question_context
from app.interview.prompt import (
    answer_schema,
    build_answer_prompt,
    build_classifier_prompt,
    classifier_schema,
)
from app.interview.ranking import rank_passages
from app.interview.render import render
from app.llm.base import LlmProvider, LlmResult, ProviderError
from app.llm.pricing import cost_usd
from app.models import LlmCall, Message, Phase, TeamDisclosure, TeamStakeholderState

log = logging.getLogger(__name__)

TSS = TeamStakeholderState
# Used when both drafts fail the checks. Several plain non-answers, not one stock line:
# step 8 showed deflections happen almost only where something is hidden, so one fixed
# sentence would tell teams "you just hit a secret". These read like an ordinary "don't
# know", which stakeholders also give when nothing is there.
DEFLECTIONS = (
    "I don't recall the details of that, I'm afraid.",
    "That's not something I can speak to.",
    "I'd have to think about that; I don't have it to hand.",
    "I'm not the right person to answer that.",
    "I can't help you there, sorry.",
)
REMINDER = (
    "\n\nBefore you answer: use only the evidence above; in paraphrase mode give no exact "
    "figures, names or phrases; never repeat internal markers; cite only ids in <evidence>."
)


@dataclass(frozen=True)
class EvidenceCard:
    passage_id: str
    record_id: str
    record_title: str
    excerpt: str  # the case's text at `mode` (render.py), never the model's wording
    new: bool  # first time this team has been shown it (or upgraded to quote)
    mode: str = "quote"  # 'quote' | 'paraphrase': the UI can label paraphrased cards


@dataclass(frozen=True)
class AskResult:
    question_id: uuid.UUID
    question_text: str
    answer_id: uuid.UUID
    answer_text: str
    status: str  # 'answered' | 'blocked' (safe deflection after a failed check)
    evidence: list[EvidenceCard]
    questions_remaining: int


def tier_for(trust: int, thresholds: dict[str, int]) -> Tier:
    if trust >= thresholds["open"]:
        return Tier.open
    if trust >= thresholds["neutral"]:
        return Tier.neutral
    return Tier.guarded


def _card(case: Case, pid: str, mode: Mode, new: bool) -> EvidenceCard:
    """`mode` is the best the team has unlocked for this passage: a card never shows more
    than the stakeholder was allowed to say (render.py)."""
    p = case.passage[pid]
    title = case.record[p.record].title
    return EvidenceCard(pid, p.record, title, render(case, pid, mode), new, mode.name)


def _thread(team: TeamContext, stakeholder_id: str):
    return (Message.team_id == team.team_id, Message.stakeholder_id == stakeholder_id)


def _pairs(rows: list[Message]) -> list[tuple[str, str]]:
    """[(question, answer), ...] from answered messages in seq order."""
    pairs, question = [], None
    for m in rows:
        if m.role == "question":
            question = m.text
        elif m.role == "answer" and question is not None:
            pairs.append((question, m.text))
            question = None
    return pairs


def _llm_rows(team, question_id, results: list[tuple[str, LlmResult]]) -> list[dict]:
    return [
        {
            "purpose": purpose,
            "provider": r.provider,
            "model": r.model,
            "team_id": team.team_id,
            "message_id": question_id,
            "input_tokens": r.usage.input_tokens,
            "cache_read_tokens": r.usage.cache_read_tokens,
            "cache_write_tokens": r.usage.cache_write_tokens,
            "output_tokens": r.usage.output_tokens,
            "cost_usd": cost_usd(r.model, r.usage),
            "latency_ms": r.latency_ms,
            "status": "ok",
            "request_id": r.request_id,
            "error": None,
        }
        for purpose, r in results
    ]


async def _replay(
    session: AsyncSession, case: Case, team: TeamContext, question: Message
) -> AskResult:
    """Same client_request_id seen before: return the stored result, charge nothing."""
    if question.status == "pending":
        raise ApiError(409, "QUESTION_IN_FLIGHT", "this question is still being answered")
    answer = await session.scalar(
        select(Message).where(
            *_thread(team, question.stakeholder_id), Message.seq == question.seq + 1
        )
    )
    state = await session.get(TSS, (team.team_id, question.stakeholder_id))
    return AskResult(
        question_id=question.id,
        question_text=question.text,
        answer_id=answer.id,
        answer_text=answer.text,
        status=answer.status,
        evidence=[_card(case, pid, False) for pid in answer.cited],
        questions_remaining=state.quota - state.questions_used,
    )


async def ask_question(
    *,
    maker: async_sessionmaker[AsyncSession],
    provider: LlmProvider,
    case: Case,
    team: TeamContext,
    stakeholder_id: str,
    client_request_id: uuid.UUID,
    text: str,
) -> AskResult:
    stakeholder = case.stakeholder.get(stakeholder_id)
    if stakeholder is None or stakeholder.company != team.company_id:
        raise ApiError(404, "NOT_FOUND", "no such stakeholder")  # same answer as nonexistent

    # ---------------------------------------------------------------- T1: reserve
    async with maker() as session, session.begin():
        earlier = await session.scalar(
            select(Message).where(
                Message.team_id == team.team_id, Message.client_request_id == client_request_id
            )
        )
        if earlier is not None:
            return await _replay(session, case, team, earlier)

        event = await current_event(session)
        config = event.config
        phase = await session.scalar(
            select(Phase).where(Phase.event_id == event.id, Phase.status == "running")
        )
        if phase is None or not phase.capabilities.get("interview"):
            raise ApiError(409, "PHASE_CLOSED", "interviews are closed in the current phase")

        question_id = uuid.uuid4()
        # One atomic statement: succeeds only if quota is left AND nothing is in flight.
        # Two laptops pressing send at once can't both pass, and nobody can exceed the quota.
        reserved = (
            await session.execute(
                update(TSS)
                .where(
                    TSS.team_id == team.team_id,
                    TSS.stakeholder_id == stakeholder_id,
                    TSS.questions_used < TSS.quota,
                    TSS.in_flight_message_id.is_(None),
                )
                .values(
                    questions_used=TSS.questions_used + 1,
                    in_flight_message_id=question_id,
                    version=TSS.version + 1,
                )
                .returning(TSS.trust, TSS.questions_used, TSS.quota)
            )
        ).one_or_none()
        if reserved is None:
            state = await session.get(TSS, (team.team_id, stakeholder_id))
            if state is None:
                raise ApiError(404, "NOT_FOUND", "no such stakeholder")
            if state.in_flight_message_id is not None:
                raise ApiError(
                    409, "QUESTION_IN_FLIGHT", "a teammate's question to this person is pending"
                )
            raise ApiError(409, "QUOTA_EXHAUSTED", "no questions left for this person")
        trust, used, quota = reserved

        seq = (
            await session.scalar(
                select(func.coalesce(func.max(Message.seq), 0)).where(
                    *_thread(team, stakeholder_id)
                )
            )
        ) + 1
        session.add(
            Message(
                id=question_id,
                team_id=team.team_id,
                stakeholder_id=stakeholder_id,
                seq=seq,
                role="question",
                text=text,
                status="pending",
                client_request_id=client_request_id,
            )
        )
        answered = (
            await session.scalars(
                select(Message)
                .where(*_thread(team, stakeholder_id), Message.status.in_(["answered", "blocked"]))
                .order_by(Message.seq)
            )
        ).all()
        disclosed_modes = {
            pid: Mode[mode]
            for pid, mode in (
                await session.execute(
                    select(TeamDisclosure.passage_id, TeamDisclosure.mode).where(
                        TeamDisclosure.team_id == team.team_id
                    )
                )
            ).all()
        }
        disclosed = set(disclosed_modes)
    history = _pairs(list(answered))
    recent_answers = [m for m in answered if m.role == "answer"][-2:]
    recently_cited = {pid for m in recent_answers for pid in m.cited}
    # every earlier answer in this thread already passed the checks (or is the deflection),
    # so repeating a figure from one of them tells the team nothing new
    already_said = "\n".join(m.text for m in answered if m.role == "answer")

    # ---------------------------------------------------------------- model calls
    results: list[tuple[str, LlmResult]] = []
    models = config["models"]
    try:
        clf = await provider.complete(
            purpose="classify",
            model=models["classify"],
            prompt=build_classifier_prompt(case, stakeholder_id, recent=history, question=text),
            schema=classifier_schema(case, stakeholder_id),
            max_tokens=200,
        )
        results.append(("classify", clf))
        vocab = set(case.company[stakeholder.company].topics)
        label = clf.data.get("label", "broad")
        topics = [t for t in clf.data.get("topics", []) if t in vocab]

        tier = tier_for(trust, config["tier_thresholds"])
        eligible = eligible_passages(
            case,
            question_context(
                stakeholder=stakeholder_id,
                tier=tier,
                label=label,
                topics=topics,
                phase=phase.key,
                disclosed=disclosed,
            ),
        )
        ranked = rank_passages(
            case,
            eligible,
            topics,
            recently_cited,
            budget_tokens=config["evidence_token_budget"],
            max_passages=config["max_passages"],
            question=text,
        )
        evidence: list[tuple[str, Mode]] = [(pid, eligible[pid]) for pid in ranked]
        provided = dict(evidence)
        prompt = build_answer_prompt(
            case, stakeholder_id, tier=tier, history=history, evidence=evidence, question=text
        )

        deflection = DEFLECTIONS[question_id.int % len(DEFLECTIONS)]
        status, reply, cards = "blocked", deflection, []
        for attempt in range(2):
            p = (
                prompt
                if attempt == 0
                else dataclasses.replace(prompt, final_user=prompt.final_user + REMINDER)
            )
            res = await provider.complete(
                purpose="answer",
                model=models["answer"],
                prompt=p,
                schema=answer_schema(case, stakeholder_id),
                max_tokens=600,
            )
            results.append(("answer", res))
            draft = str(res.data.get("reply", ""))
            cited = [str(c) for c in res.data.get("cited", [])]
            check = check_reply(
                case, stakeholder_id, provided, cited, draft, already_said, question=text
            )
            if check.ok:
                status, reply, cards = "answered", draft, check.cards
                if check.auto_cited:
                    log.info("auto-cited (used, not cited): %s", check.auto_cited)
                break
            log.warning("reply blocked (%s): %s", attempt, check.details)
    except ProviderError as exc:
        await _refund(maker, team, stakeholder_id, question_id, results, exc)
        raise ApiError(
            503,
            "PROVIDER_BUSY",
            "the stakeholder is busy; try again in a few seconds (no question was used)",
            {"retry_after_seconds": 5},
        ) from exc

    # ---------------------------------------------------------------- T2: save
    async with maker() as session, session.begin():
        answer = Message(
            team_id=team.team_id,
            stakeholder_id=stakeholder_id,
            seq=seq + 1,
            role="answer",
            text=reply,
            status=status,
            cited=[c.passage_id for c in cards],
        )
        session.add(answer)
        await session.flush()
        await session.execute(
            update(Message).where(Message.id == question_id).values(status="answered")
        )
        new_ids: set[str] = set()
        if cards:
            # first discovery only: rows that already exist are skipped, and RETURNING
            # tells us which ones were genuinely new for this team
            # first discovery inserts; a paraphrase-only discovery later given in quote mode
            # is upgraded. RETURNING lists both, so both show as new cards.
            stmt = insert(TeamDisclosure).values(
                [
                    {
                        "team_id": team.team_id,
                        "passage_id": c.passage_id,
                        "stakeholder_id": stakeholder_id,
                        "message_id": answer.id,
                        "mode": provided[c.passage_id].name,
                    }
                    for c in cards
                ]
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[TeamDisclosure.team_id, TeamDisclosure.passage_id],
                set_={"mode": stmt.excluded.mode},
                where=(TeamDisclosure.mode == "paraphrase") & (stmt.excluded.mode == "quote"),
            )
            inserted = await session.scalars(stmt.returning(TeamDisclosure.passage_id))
            new_ids = set(inserted.all())
        await session.execute(insert(LlmCall).values(_llm_rows(team, question_id, results)))
        await session.execute(
            update(TSS)
            .where(
                TSS.team_id == team.team_id,
                TSS.stakeholder_id == stakeholder_id,
                TSS.in_flight_message_id == question_id,
            )
            .values(in_flight_message_id=None)
        )

    return AskResult(
        question_id=question_id,
        question_text=text,
        answer_id=answer.id,
        answer_text=reply,
        status=status,
        evidence=[
            _card(
                case,
                c.passage_id,
                max(provided[c.passage_id], disclosed_modes.get(c.passage_id, Mode.aware)),
                c.passage_id in new_ids,
            )
            for c in cards
        ],
        questions_remaining=quota - used,
    )


async def _refund(maker, team, stakeholder_id, question_id, results, exc: Exception) -> None:
    """The model failed: undo the reservation as if the question was never asked."""
    async with maker() as session, session.begin():
        await session.execute(delete(Message).where(Message.id == question_id))
        await session.execute(
            update(TSS)
            .where(
                TSS.team_id == team.team_id,
                TSS.stakeholder_id == stakeholder_id,
                TSS.in_flight_message_id == question_id,
            )
            .values(questions_used=TSS.questions_used - 1, in_flight_message_id=None)
        )
        rows = _llm_rows(team, None, results)
        # a multi-row INSERT needs every row to have the same keys, so the error row
        # is a full row too
        rows.append(
            {
                "purpose": "answer" if results else "classify",
                "provider": "unknown",
                "model": "unknown",
                "team_id": team.team_id,
                "message_id": None,
                "input_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0,
                "latency_ms": 0,
                "status": "error",
                "request_id": None,
                "error": str(exc)[:500],
            }
        )
        await session.execute(insert(LlmCall).values(rows))


async def conversation(
    maker: async_sessionmaker[AsyncSession], case: Case, team: TeamContext, stakeholder_id: str
) -> list[dict]:
    """The thread as any laptop of the team sees it: messages in order, answers with cards."""
    stakeholder = case.stakeholder.get(stakeholder_id)
    if stakeholder is None or stakeholder.company != team.company_id:
        raise ApiError(404, "NOT_FOUND", "no such stakeholder")
    async with maker() as session:
        rows = (
            await session.scalars(
                select(Message)
                .where(*_thread(team, stakeholder_id), Message.status != "pending")
                .order_by(Message.seq)
            )
        ).all()
        modes = {
            pid: Mode[mode]
            for pid, mode in (
                await session.execute(
                    select(TeamDisclosure.passage_id, TeamDisclosure.mode).where(
                        TeamDisclosure.team_id == team.team_id
                    )
                )
            ).all()
        }
    return [
        {
            "id": m.id,
            "seq": m.seq,
            "role": m.role,
            "text": m.text,
            # cards re-render at the team's best mode now, so an upgrade shows everywhere
            "evidence": [
                _card(case, pid, modes.get(pid, Mode.paraphrase), False) for pid in m.cited
            ],
        }
        for m in rows
    ]
