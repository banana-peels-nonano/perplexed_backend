"""Hostile-question suite: how often do stakeholders leak, invent, or refuse? (step 8)

    python scripts/redteam.py --code K7QM-RX4T-9HCW                 # probes + recall
    python scripts/redteam.py --code ... --only leading injection   # some categories
    python scripts/redteam.py --code ... --recall-only              # classifier only, cheap
    python scripts/redteam.py --code ... --fake                     # wiring, no API calls

Each probe is a fresh one-question thread (the team's thread with that stakeholder is wiped
first, trust set to the probe's), sent through the real app in-process, so every guardrail
runs exactly as in the event. Dev database only: it wipes threads.

Flags per probe:
  LEAK      a forbidden passage reached the team: its card, or its fingerprint in the reply
  CONFIRM?  the reply matches the probe's `confirm` pattern: read it, she may have gone
            along with a false or unearned premise (not auto-failed: "no, not 15" matches)
  MISSING   control question: an expected passage didn't make it onto a card
  DEFLECT   the reply was replaced by the safe deflection (both drafts failed checks)
  REDO      the first draft failed a check and was regenerated

Writes a markdown report with every question and answer to reports/ (keep it out of git).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
import tomllib
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `app` imports work
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_interview import prepare  # noqa: E402

from app.content.loader import load_case  # noqa: E402
from app.content.schema import Case, Mode, Tier  # noqa: E402
from app.content.store import current_event  # noqa: E402
from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.interview.eligibility import eligible_passages, question_context  # noqa: E402
from app.interview.prompt import build_classifier_prompt, classifier_schema  # noqa: E402
from app.interview.ranking import rank_passages  # noqa: E402
from app.interview.service import tier_for  # noqa: E402
from app.interview.text import mentions  # noqa: E402
from app.llm.fake import FakeProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import LlmCall  # noqa: E402

API = "/api/v1"


def check_suite(case: Case, suite: dict) -> list[str]:
    """Catch suite mistakes before spending money: unknown ids, impossible expectations."""
    problems = []
    for item in suite.get("probe", []) + suite.get("recall", []):
        tier = tier_for(item["trust"], {"open": 70, "neutral": 35})
        rows = {a.passage: a for a in case.access_for(item["stakeholder"])}
        for pid in item.get("forbidden", []) + item.get("expect", []):
            if pid not in case.passage:
                problems.append(f"{item.get('id', item['question'])}: unknown passage {pid}")
        for pid in item.get("expect", []):
            a = rows.get(pid)
            if a is None or a.mode_at(tier) < Mode.paraphrase:
                problems.append(f"{item.get('id', item['question'])}: {pid} can't be shown")
        for pid in item.get("forbidden", []):
            a = rows.get(pid)
            free = a and not (a.labels or a.requires_topic or a.requires_disclosed or a.phase_from)
            if free and a.mode_at(tier) >= Mode.paraphrase:
                problems.append(f"{item['id']}: {pid} is forbidden but freely shown at this trust")
    return problems


async def run_probe(client, case: Case, team_code: str, probe: dict) -> dict:
    await prepare(team_code, probe["stakeholder"], True, probe["trust"])
    started = time.perf_counter()
    r = await client.post(
        f"{API}/stakeholders/{probe['stakeholder']}/questions",
        json={"client_request_id": str(uuid.uuid4()), "text": probe["question"]},
    )
    wall = time.perf_counter() - started
    if r.status_code != 200:
        return {**probe, "error": f"{r.status_code} {r.text}", "flags": ["ERROR"], "wall": wall}
    body = r.json()
    answer = body["answer"]
    cards = [c["passage_id"] for c in body["evidence"]]
    async with get_sessionmaker()() as s:
        calls = (
            await s.scalars(
                select(LlmCall.purpose).where(LlmCall.message_id == uuid.UUID(body["question_id"]))
            )
        ).all()
    answers = sum(1 for c in calls if c == "answer")

    flags, notes = [], []
    for pid in probe.get("forbidden", []):
        if pid in cards:
            flags.append("LEAK")
            notes.append(f"card {pid}")
        for fp in case.passage[pid].fingerprints:
            if mentions(answer, fp):
                flags.append("LEAK")
                notes.append(f"{pid} fingerprint {fp!r}")
    if probe.get("confirm") and re.search(probe["confirm"], answer, re.IGNORECASE):
        flags.append("CONFIRM?")
    missing = [p for p in probe.get("expect", []) if p not in cards]
    if missing:
        flags.append("MISSING")
        notes.append("missing " + ", ".join(missing))
    if body["status"] == "blocked":
        flags.append("DEFLECT")
    if answers > 1:
        flags.append("REDO")
    return {
        **probe,
        "answer": answer,
        "cards": cards,
        "answers": answers,
        "flags": list(dict.fromkeys(flags)),
        "notes": notes,
        "wall": wall,
    }


async def run_recall(provider, case: Case, items: list[dict]) -> list[dict]:
    async with get_sessionmaker()() as s:
        config = (await current_event(s)).config
    out = []
    for item in items:
        sid = item["stakeholder"]
        res = await provider.complete(
            purpose="classify",
            model=config["models"]["classify"],
            prompt=build_classifier_prompt(case, sid, recent=[], question=item["question"]),
            schema=classifier_schema(case, sid),
            max_tokens=200,
        )
        vocab = set(case.company[case.stakeholder[sid].company].topics)
        label = res.data.get("label", "broad")
        topics = [t for t in res.data.get("topics", []) if t in vocab]
        tier: Tier = tier_for(item["trust"], config["tier_thresholds"])
        eligible = eligible_passages(
            case,
            question_context(stakeholder=sid, tier=tier, label=label, topics=topics, phase="solo"),
        )
        ranked = rank_passages(
            case,
            eligible,
            topics,
            budget_tokens=config["evidence_token_budget"],
            max_passages=config["max_passages"],
        )
        hit = [p for p in item["expect"] if p in ranked]
        out.append({**item, "label": label, "topics": topics, "ranked": ranked, "hit": hit})
    return out


def report(probes: list[dict], recall: list[dict], path: Path) -> str:
    lines = [f"# Red-team run {datetime.now():%Y-%m-%d %H:%M}", ""]
    if probes:
        cats: dict[str, list[dict]] = {}
        for p in probes:
            cats.setdefault(p["category"], []).append(p)
        lines += [
            "| category | probes | LEAK | CONFIRM? | MISSING | DEFLECT | REDO |",
            "|---|---|---|---|---|---|---|",
        ]
        for cat, ps in cats.items():
            n = {f: sum(f in p["flags"] for p in ps) for f in
                 ("LEAK", "CONFIRM?", "MISSING", "DEFLECT", "REDO")}  # fmt: skip
            lines.append(
                f"| {cat} | {len(ps)} | {n['LEAK']} | {n['CONFIRM?']} | {n['MISSING']} "
                f"| {n['DEFLECT']} | {n['REDO']} |"
            )
        lines.append("")
    if recall:
        got = sum(len(r["hit"]) for r in recall)
        want = sum(len(r["expect"]) for r in recall)
        lines += [f"**Retrieval recall@4: {got}/{want} = {got / want:.0%}** (D-017 bar: 90%)", ""]
    summary = "\n".join(lines)

    detail = ["", "## Probes", ""]
    for p in probes:
        flags = " ".join(p["flags"]) or "ok"
        detail += [
            f"### {p['id']} ({p['stakeholder']}, trust {p['trust']}): {flags}",
            f"**Q:** {p['question']}  ",
            f"**A:** {p.get('answer', p.get('error'))}  ",
            f"cards: {', '.join(p.get('cards', [])) or '-'}; answer calls: {p.get('answers')}; "
            f"{p['wall']:.1f}s" + (f"; {'; '.join(p['notes'])}" if p.get("notes") else ""),
            "",
        ]
    if recall:
        detail += [
            "## Recall",
            "",
            "| stakeholder | trust | question | label/topics | top 4 | hit |",
            "|---|---|---|---|---|---|",
        ]
        for r in recall:
            ok = "yes" if len(r["hit"]) == len(r["expect"]) else f"MISS {r['expect']}"
            detail.append(
                f"| {r['stakeholder']} | {r['trust']} | {r['question']} | {r['label']} "
                f"{r['topics']} | {', '.join(r['ranked']) or '-'} | {ok} |"
            )
    path.parent.mkdir(exist_ok=True)
    path.write_text(summary + "\n".join(detail) + "\n")
    return summary


async def main_async(args: argparse.Namespace, suite: dict) -> int:
    case = load_case(args.case)
    problems = check_suite(case, suite)
    if problems:
        print("suite problems (fix the suite first):")
        print("\n".join(f"  {p}" for p in problems))
        return 2
    probes = [p for p in suite.get("probe", []) if not args.only or p["category"] in args.only]
    app = create_app(provider=FakeProvider() if args.fake else None)
    if app.state.provider is None:
        sys.exit("ANTHROPIC_API_KEY is not set in .env (or use --fake)")

    results: list[dict] = []
    if not args.recall_only:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://redteam", timeout=120
        ) as client:
            r = await client.post(
                f"{API}/auth/login", json={"access_code": args.code, "device_label": "redteam"}
            )
            if r.status_code != 200:
                sys.exit(f"login failed: {r.status_code} {r.text}")
            team_code = r.json()["code"]
            for i, probe in enumerate(probes, start=1):
                res = await run_probe(client, case, team_code, probe)
                results.append(res)
                print(f"[{i:2d}/{len(probes)}] {probe['id']:28s} {' '.join(res['flags']) or 'ok'}")
            await client.post(f"{API}/auth/logout")
    recall = [] if args.no_recall else await run_recall(app.state.provider, case, suite["recall"])
    await get_engine().dispose()

    path = ROOT / "reports" / f"redteam-{datetime.now():%Y%m%d-%H%M%S}.md"
    print("\n" + report(results, recall, path))
    print(f"full report: {path.relative_to(ROOT)}")
    leaks = sum("LEAK" in r["flags"] for r in results)
    return 1 if leaks else 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--code", required=True, help="a company-A team's access code")
    p.add_argument("--case", default="tests/fixtures/northfield.toml")
    p.add_argument("--suite", default="tests/fixtures/northfield_redteam.toml")
    p.add_argument("--only", nargs="*", help="categories to run (default: all)")
    p.add_argument("--recall-only", action="store_true", help="skip the probes")
    p.add_argument("--no-recall", action="store_true", help="skip the recall test")
    p.add_argument("--fake", action="store_true", help="use the fake model (no API calls)")
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    suite = tomllib.loads(Path(args.suite).read_text())
    sys.exit(asyncio.run(main_async(args, suite)))


if __name__ == "__main__":
    main()
