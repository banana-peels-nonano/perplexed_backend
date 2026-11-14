"""First real interview: the fixture's scripted questions, against the real model.

    python scripts/smoke_interview.py --code K7QM-RX4T-9HCW --reset
    python scripts/smoke_interview.py --code K7QM-RX4T-9HCW --reset --trust 80   # open tier
    python scripts/smoke_interview.py --code ... --fake                          # no API calls

It goes through the real app (login -> POST questions -> GET messages) in-process, against
the database in DATABASE_URL, with the provider configured in .env. Then it prints each
answer with its evidence cards, the measured tokens, cost and latency, and a few checks.

--reset   dev only: wipes this team's thread with the stakeholder, its discovered evidence,
          and resets its question count and trust, so every run starts the same way.
--trust N dev only: sets the team's hidden trust with this stakeholder before the run
          (50 = neutral, 70+ = open with the default thresholds).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import delete, func, select, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `app` imports work

from app.content.loader import load_case  # noqa: E402
from app.content.store import current_event  # noqa: E402
from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.interview.disclosure import mentions  # noqa: E402
from app.llm.fake import FakeProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import LlmCall, Message, Team, TeamDisclosure, TeamStakeholderState  # noqa: E402

API = "/api/v1"


async def prepare(code_team: str, stakeholder: str, reset: bool, trust: int | None) -> None:
    async with get_sessionmaker()() as s, s.begin():
        team = await s.scalar(select(Team).where(Team.code == code_team))
        if team is None:
            sys.exit(f"no team {code_team!r} in the database")
        if reset:
            await s.execute(delete(TeamDisclosure).where(TeamDisclosure.team_id == team.id))
            await s.execute(
                delete(Message).where(
                    Message.team_id == team.id, Message.stakeholder_id == stakeholder
                )
            )
        values: dict = {}
        if reset:  # back to how the event starts, trust included (it outlives a --trust run)
            start = (await current_event(s)).config.get("trust_start", 50)
            values.update(questions_used=0, in_flight_message_id=None, trust=start)
        if trust is not None:
            values["trust"] = trust
        if values:
            await s.execute(
                update(TeamStakeholderState)
                .where(
                    TeamStakeholderState.team_id == team.id,
                    TeamStakeholderState.stakeholder_id == stakeholder,
                )
                .values(**values)
            )


async def run(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    convo = case.smoke_conversation[0]
    app = create_app(provider=FakeProvider() if args.fake else None)
    if app.state.provider is None:
        sys.exit("ANTHROPIC_API_KEY is not set in .env (or use --fake)")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://smoke", timeout=120
    ) as client:
        r = await client.post(
            f"{API}/auth/login", json={"access_code": args.code, "device_label": "smoke"}
        )
        if r.status_code != 200:
            sys.exit(f"login failed: {r.status_code} {r.text}")
        team_code = r.json()["code"]
        await prepare(team_code, convo.stakeholder, args.reset, args.trust)

        answers: list[dict] = []
        question_ids: list[str] = []
        for i, q in enumerate(convo.questions, start=1):
            started = time.perf_counter()
            r = await client.post(
                f"{API}/stakeholders/{convo.stakeholder}/questions",
                json={"client_request_id": str(uuid.uuid4()), "text": q},
            )
            wall = time.perf_counter() - started
            print(f"\nQ{i} ({wall:.2f}s) > {q}")
            if r.status_code != 200:
                print(f"   ERROR {r.status_code}: {r.text}")
                answers.append({})
                continue
            body = r.json()
            answers.append(body)
            question_ids.append(body["question_id"])
            print(f"A{i} [{body['status']}] {body['answer']}")
            for card in body["evidence"]:
                flag = "NEW " if card["new"] else ""
                print(f"   evidence {flag}{card['passage_id']} ({card['record_title']}):")
                print(f"      {card['excerpt']}")
            print(f"   questions remaining: {body['questions_remaining']}")
        await client.post(f"{API}/auth/logout")

    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(
                    LlmCall.purpose,
                    LlmCall.status,
                    func.count(),
                    func.sum(LlmCall.input_tokens),
                    func.sum(LlmCall.cache_read_tokens),
                    func.sum(LlmCall.cache_write_tokens),
                    func.sum(LlmCall.output_tokens),
                    func.sum(LlmCall.cost_usd),
                    func.avg(LlmCall.latency_ms),
                )
                .where(LlmCall.message_id.in_([uuid.UUID(x) for x in question_ids]))
                .group_by(LlmCall.purpose, LlmCall.status)
            )
        ).all()
        errors = (
            await s.scalars(
                select(LlmCall.error)
                .where(LlmCall.status == "error")
                .order_by(LlmCall.created_at.desc())
                .limit(3)
            )
        ).all()
    await get_engine().dispose()

    print("\nmodel calls for this run:")
    print("  purpose  status calls  uncached_in cache_read cache_write  out     cost_usd  avg_ms")
    for purpose, status, n, inp, cr, cw, out, cost, ms in rows:
        print(f"  {purpose:8s} {status:6s} {n:5d} {inp:12d} {cr:10d} {cw:11d} {out:5d} "
              f"{cost:11.6f} {ms:7.0f}")  # fmt: skip
    if errors:
        print("\nrecent provider errors:")
        for e in errors:
            print(f"  {e}")

    # quick checks; a human still reads the answers above
    texts = [a.get("answer", "") for a in answers]
    open_tier = args.trust is not None and args.trust >= 70
    checks = {
        "all three questions answered": all(a.get("status") == "answered" for a in answers),
        "Q3 recalls the Q1 figure (memory)": len(texts) > 2 and "41.7" in texts[2],
        "no exact QA cut leaked at this trust": open_tier
        or not mentions(" ".join(texts), "15 percent"),
    }
    print("\nchecks:")
    for name, ok in checks.items():
        print(f"  [{'ok' if ok else '!!'}] {name}")
    return 0 if all(checks.values()) else 1


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--code", required=True, help="the team's access code (from the seed output)")
    p.add_argument("--case", default="tests/fixtures/northfield.toml")
    p.add_argument("--reset", action="store_true", help="dev only: start the thread from scratch")
    p.add_argument("--trust", type=int, help="dev only: set hidden trust (0-100) before the run")
    p.add_argument("--fake", action="store_true", help="use the fake model (no API calls)")
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
