"""POST /stakeholders/{id}/questions and GET /stakeholders/{id}/messages."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import ApiError
from app.auth.context import CurrentTeam
from app.content.schema import Case
from app.db import session_factory
from app.interview.service import ask_question, conversation
from app.llm.base import LlmProvider

router = APIRouter(tags=["interview"])

Maker = Annotated[async_sessionmaker[AsyncSession], Depends(session_factory)]


async def get_case(request: Request, maker: Maker) -> Case:
    return await request.app.state.content.get(maker)


def get_provider(request: Request) -> LlmProvider:
    provider = request.app.state.provider
    if provider is None:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "no model provider is configured on the server")
    return provider


CaseDep = Annotated[Case, Depends(get_case)]
ProviderDep = Annotated[LlmProvider, Depends(get_provider)]


class AskIn(BaseModel):
    # The frontend makes a new UUID per question and reuses it if it retries the SAME
    # question (e.g. after a network drop), so a retry never costs a second question.
    client_request_id: uuid.UUID
    text: str = Field(min_length=1, max_length=400)


class EvidenceOut(BaseModel):
    passage_id: str
    record_id: str
    record_title: str
    excerpt: str
    new: bool
    mode: str  # 'quote' | 'paraphrase'


class AskOut(BaseModel):
    question_id: uuid.UUID
    answer_id: uuid.UUID
    answer: str
    # no `status`: a participant must not be able to tell a safe deflection from a normal
    # answer (it would mark exactly where the secrets are); organisers see message.status
    evidence: list[EvidenceOut]
    questions_remaining: int
    # 'answer', or 'hr_notice' when the stakeholder walked out over the team's conduct.
    # Deliberately visible (a consequence of conduct), unlike deflections (FRONTEND.md)
    kind: str


class MessageOut(BaseModel):
    id: uuid.UUID
    seq: int
    role: str
    text: str
    evidence: list[EvidenceOut]


@router.post("/stakeholders/{stakeholder_id}/questions", response_model=AskOut)
async def ask(
    stakeholder_id: str,
    body: AskIn,
    team: CurrentTeam,
    maker: Maker,
    case: CaseDep,
    provider: ProviderDep,
    request: Request,
) -> AskOut:
    result = await ask_question(
        maker=maker,
        provider=provider,
        case=case,
        team=team,
        stakeholder_id=stakeholder_id,
        client_request_id=body.client_request_id,
        text=body.text,
    )
    # the team's other laptop refetches; this one already has the answer
    events = [{"type": "thread", "stakeholder_id": stakeholder_id}, {"type": "stakeholders"}]
    if any(c.new for c in result.evidence):
        events.append({"type": "evidence"})
    await request.app.state.hub.publish(team.team_id, *events)
    return AskOut(
        question_id=result.question_id,
        answer_id=result.answer_id,
        answer=result.answer_text,
        evidence=[EvidenceOut(**vars(c)) for c in result.evidence],
        questions_remaining=result.questions_remaining,
        kind=result.kind,
    )


@router.get("/stakeholders/{stakeholder_id}/messages", response_model=list[MessageOut])
async def messages(
    stakeholder_id: str, team: CurrentTeam, maker: Maker, case: CaseDep
) -> list[MessageOut]:
    rows = await conversation(maker, case, team, stakeholder_id)
    return [
        MessageOut(**{**m, "evidence": [EvidenceOut(**vars(c)) for c in m["evidence"]]})
        for m in rows
    ]
