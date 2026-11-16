# PERPLEXED 2.0 — Engineering Status

Last updated: 16 November 2026

## 1. Project objective

Build the backend for PERPLEXED 2.0, supporting team investigation, AI
stakeholder interviews, evidence discovery, alliances, submissions, judging
and event administration.

Hard deadline: Thursday, 19 November 2026. Frontend API stubs due Monday
17 November, noon. Rehearsal planned for Wednesday 18 November.

## 2. Environment

- OS: Ubuntu 24.04.5 LTS
- Python: 3.12.3
- Git: 2.43.0
- Docker: 29.9.2
- Docker Compose: 5.6.1
- Docker access: sudo; no Docker-group membership
- PostgreSQL 17 in Docker, published on 127.0.0.1 only; separate test database

## 3. Architecture decisions

Full reasoning for each is kept in the engineering dossier (D-001 to D-023).

- Modular monolith: one FastAPI service, async throughout.
- PostgreSQL as the only datastore. **No pgvector, Redis or Celery**: retrieval is
  deterministic, and the event's bottleneck is provider rate limits, not our servers.
- SQLAlchemy 2 (async) and Alembic; never hold a database connection across a model call.
- **Model:** Claude Haiku 5.5 for answers and for question classification, with
  low effort, no extended thinking, JSON-schema output and prompt caching.
  Gemini is a manual fallback. No load balancer: one provider account, measured headroom.
- **Retrieval:** eligibility rules decide what a stakeholder may reveal (company,
  access by trust tier, unlock rules, phase); ranking orders it (topics, then
  full-text); a token budget caps it. Participants never browse records.
- **Disclosure:** the model cites; the database quotes. Cards are rendered from
  the case at the permitted mode; replies are checked in code before anyone sees
  them.
- Full conversation history per thread (no sliding window); the quota bounds its length.
- Sessions are server-side (HttpOnly cookie, hashed token, at most 2 per team).
- Real case content is never committed to this repository.
- Backend repository kept separate from the frontend.

## 4. Security principles

- Server-side company isolation; another company's resources answer 404.
- Hidden trust and unrevealed evidence never reach participants, including through
  evidence cards or response metadata.
- Participant input is escaped and treated as data, never as instructions.
- Every reply is checked for leaked passages, figures in paraphrase mode, invented
  facts and the stakeholder's internal marker.
- Secrets excluded from Git; `.env` is mode 600; dev and event API keys kept apart.
- Local database bound to loopback only; no Docker-group membership.
- Every model call is recorded with tokens, cost and latency.

## 5. Implementation progress

| Component | Status | Notes |
|---|---|---|
| Development environment | Done | Docker, Postgres 17, venv, test database |
| FastAPI foundation | Done | settings, error envelope, health check |
| Database schema and migrations | Done | migrations 0001–0006 |
| Case format, loader, seed | Done | validated TOML; placeholder case only |
| Authentication and teams | Done | access codes, sessions, team context |
| Interview flow | Done | quota, idempotency, one question in flight, refund on failure, memory |
| Retrieval | Done | recall 17/17 on the placeholder case |
| Disclosure and grounding checks | Done | leak, paraphrase, ungrounded-fact, citation checks; safe non-answers |
| LLM provider layer | Done | Anthropic provider, fake provider for tests, cost accounting |
| Hostile-question suite | Done | 27 probes, 0 leaks on the placeholder case |
| Event engine (phases) | Done | start/pause/resume/extend/end; timed phases close interviews on time |
| Trust and HR mechanics | Done | per-question trust moves; HR per team per department (warning, complaint, 10-minute department lockout); every strike logged for efficiency scoring; numbers are defaults in event config until the seniors confirm |
| Portal endpoints | Done | status (the team's own clock), evidence list, board, case brief, announcements, projector display |
| Evidence board | Partial | storage and checks done; UI design pending (frontend team) |
| Live updates (WebSockets) | Done | notify-then-refetch; session-cookie auth, origin check, tab cap, logout closes that laptop's sockets |
| Admin and organiser endpoints | Done | token-protected: team registration, balanced company assignment, time back per team, announcements, prompt-attack alerts, HR log, clues found, team view with per-question decisions, fixes, live config, content reload, costs (ORGANISERS.md) |
| Real case conversion | Pending | case expected Monday |
| Alliances | Pending | |
| Submissions and judging | Pending | |
| Frontend integration | Pending | frontend owned by another team member |
| Staging and production deployment | Pending | |

Tests: 160 passing (`python -m pytest -q`).

## 6. Measured so far (placeholder case)

- Cost per question: $0.00020–0.00025 over full 10-question threads.
- Event estimate: $1.70–2.10 for 150 teams × 4–5 stakeholders × 10 questions;
  about +$0.75 if real records fill the evidence budget.
- Peak load estimate (450 questions/min): ~520k input tokens/min, ~1,040 requests/min,
  ~120k output tokens/min. Compare against the account's real limits before the event.
- Latency: about 3–5 s per question (classification + answer); +2 s when a reply
  is regenerated.

These must be re-measured on the real case.

## 7. Next

1. Alliances: cross-company requests, mutual lock, organiser pairing (incl. one
   three-team alliance), partner evidence after the lock.
2. Submissions: Audit Findings Sheet with versions, hard deadline, cross-company finding,
   citations.
3. Judging: judge accounts, Round 1 and Round 2 rubrics, efficiency data, tie-breakers,
   final score.
4. Deployment on an Azure for Students VM (Docker Compose, Caddy, backups), load test.
5. Real case conversion, its own hostile-question suite and recall test; rehearsal.
6. Frontend integration once the frontend arrives (FRONTEND.md, OpenAPI at `/docs`).

## 8. Event-rule questions to resolve

- Number of teams (40–70 or 100+).
- Question quota per stakeholder.
- Trust rules and HR: how much each kind of question moves trust, strikes before a
  complaint (default 3), department lockout (default 10 minutes, real time; does it pause
  with the phase?); see `app/interview/trust.py`.
- Final phase names and durations; event date and timings.
- Two-round versus four-round format; team-size policy.
- Whether submissions can be revised.
- Three-team alliance scoring; rubric criterion names.
- Evidence board behaviour (frontend design).

## 9. Current blockers

- Real stakeholder, clue and company-record content (expected Monday).
- Event-format clarifications above.
- Azure for Students sign-up (chosen host; Render free as staging fallback).
- Provider rate limits for the event key.
