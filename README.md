# PERPLEXED 2.0 — Backend

Backend for PERPLEXED 2.0, an AI consulting competition. Teams investigate a
simulated organisation by interviewing its employees, who are AI agents.

> We are not building a chatbot that answers questions about documents. We are
> building a simulated organisation whose employees are AI agents, whose internal
> reality is represented by backend-held evidence, and whose participants
> investigate that organisation by interviewing those employees.

Participants never open records. Everything they learn comes through interviews:
what a stakeholder may reveal depends on the company, the stakeholder, how much
that stakeholder trusts the team, and the phase of the event. Evidence a
stakeholder discloses becomes an evidence card on the team's board.

## How a question is answered

```
POST /api/v1/stakeholders/{id}/questions
  1. reserve      atomic quota check; one question in flight per team and stakeholder
  2. classify     a small model call: question label and topics (sees no evidence)
  3. eligibility  deterministic: which passages this stakeholder may reveal to this
                  team now, and how (quote / paraphrase / aware / deny)
  4. ranking      topic match plus full-text tie-break, capped by a token budget
  5. answer       the model answers in character, as structured {reply, cited}
  6. check        in code: leaks, figures in paraphrase mode, ungrounded facts,
                  citations; regenerate once, else a safe non-answer
  7. save         answer, evidence cards and first discoveries; refund if the model failed
```

Cards always carry the case's own text, rendered at the mode the stakeholder was
allowed (paraphrase mode withholds every figure), never the model's wording.

## Stack

- Python 3.12, FastAPI (async), Uvicorn
- PostgreSQL 17 (Docker, loopback only), SQLAlchemy 2 (async, asyncpg), Alembic
- Claude Haiku 5.5 for answers and classification (prompt caching, structured
  output); no Gemini failover is configured
- pytest + pytest-asyncio + HTTPX; Ruff

No vector database: retrieval is deterministic (eligibility rules, topics,
full-text), which keeps disclosure auditable.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env && chmod 600 .env    # then fill in the values
sudo docker compose up -d                 # PostgreSQL 17 on 127.0.0.1:5432
sudo docker compose ps                    # wait for "healthy"

alembic upgrade head
python -m app.seed tests/fixtures/northfield.toml --teams-per-company 2 --start-phase solo
uvicorn app.main:app --reload             # API docs at http://127.0.0.1:8000/docs
# one process only (no --workers): live notifications are kept in this process's memory
```

For the event, organisers create teams and access codes in the console's **Teams** tab.
Each team's code stays visible in the Teams table (and in **Download codes CSV**) so
organisers can read it out; login still checks a hash, and a plain copy is kept for the
console (migration 0011). There is no self-registration or email. Re-seeding keeps
existing codes unless you pass `--rotate-codes`.

## Checks

```bash
python -m pytest -q          # uses TEST_DATABASE_URL; never touches the dev database
ruff check . && ruff format --check .
```

## Dev tools (dev database only)

| Command                                                                                  | What it does                                                                                                                              |
| ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `python scripts/smoke_interview.py --code <A01> --reset [--conversation 1] [--trust 80]` | A scripted interview through the real app and model, with per-question tokens, cost and latency                                           |
| `python scripts/redteam.py --code <A01>`                                                 | Hostile-question suite (leading, fishing, injection, cross-company, relayed, controls) plus a retrieval recall test; report in `reports/` |
| `python scripts/cost_probe.py --dry-run --configs all`                                   | Token and cost projections for an event size                                                                                              |

Add `--fake` to the first two to run without API calls.

## API (so far)

| Method | Path                                  |                                                                                       |
| ------ | ------------------------------------- | ------------------------------------------------------------------------------------- |
| POST   | `/api/v1/auth/login`                  | access code → session cookie (`pxd_session`, HttpOnly)                                |
| POST   | `/api/v1/auth/logout`                 |                                                                                       |
| GET    | `/api/v1/me`                          | the team behind the session                                                           |
| GET    | `/api/v1/stakeholders`                | this team's company's stakeholders, with questions left                               |
| GET    | `/api/v1/stakeholders/{id}`           | one stakeholder                                                                       |
| POST   | `/api/v1/stakeholders/{id}/questions` | ask; returns the answer and evidence cards                                            |
| GET    | `/api/v1/stakeholders/{id}/messages`  | the team's thread with that stakeholder                                               |
| GET    | `/api/v1/status`                      | phase, time left, questions left per stakeholder, evidence count                      |
| GET    | `/api/v1/evidence`                    | every card the team has been shown, from any stakeholder                              |
| GET    | `/api/v1/records`                     | the records tab: off, titles only, or the documents in full (organisers' switch)      |
| GET    | `/api/v1/board`                       | the team's evidence board                                                             |
| PUT    | `/api/v1/board`                       | save the board; send the `version` you edited (409 if someone saved first)            |
| GET    | `/api/v1/brief`                       | the case brief and the team's company brief                                           |
| GET    | `/api/v1/announcements`               | organiser broadcasts for this team                                                    |
| GET    | `/api/v1/display`                     | no login: the projector page (phase, clock, public broadcasts)                        |
| WS     | `/api/v1/ws`                          | live "what changed" notifications for the team's open tabs                            |

Errors are always `{"error": {"code", "message", "details"}}`. Another company's
stakeholder is a 404.

The board holds evidence cards (only passages the team has been shown), quote cards
(the server cuts the text from the team's own answers), notes, team-drawn labelled
links, and a small free-form `extra` object for the UI. The server never computes links.

## Case content

A case is one TOML file validated by `app/content/schema.py`: companies,
stakeholders (persona, beliefs), records split into passages (topics, protected
phrases, optional authored paraphrase), per-stakeholder access by trust tier with
unlock rules, a shared brief and one brief per company, and the judges' clue list. The loader rejects inconsistent cases and names the problem.

Each stakeholder may also have a `[stakeholder.<id>.look]` table describing their portrait:
`skin` (1–6), `hair`, `hair_color`, `facial_hair`, `glasses`, `outfit`, `outfit_color`,
`background` (`#rrggbb`), `accessory`, `age`, `expression`. The allowed values are in
`app/content/schema.py` (`Look`). Anything left out is picked from the stakeholder's id.
It is art direction only: never part of any prompt.

`tests/fixtures/northfield.toml` is a **placeholder** case for development and
tests. The real case is never committed: keep it under `content/`, which is
git-ignored.

## Layout

```
app/api/         HTTP routes and the error envelope
app/auth/        access codes, sessions, team context
app/content/     case schema, loader, in-memory content store
app/interview/   eligibility, ranking, prompts, rendering, disclosure and grounding checks, the ask service
app/llm/         provider interface, Anthropic provider, fake provider, pricing
app/models/      SQLAlchemy models (content and runtime)
app/portal/      phase clock and evidence-board rules
alembic/         migrations
scripts/         dev tools
tests/           pytest suite and fixtures
```

## Frontend (in this repo)

`web/` is the whole frontend: plain ES modules with Preact + htm vendored in
`web/vendor/` (no npm, no build step), fonts self-hosted in `web/fonts/` (OFL). The backend
serves it, so the browser sees one origin and the session cookie just works.

| URL         |                                                                                            |
| ----------- | ------------------------------------------------------------------------------------------ |
| `/`         | team portal: login, brief, people (badges), interviews, evidence, board, documents, rounds |
| `/console`  | organiser console (asks for `ADMIN_TOKEN`; kept in that tab only)                          |
| `/display`  | projector page: round, clock, public announcements (no login)                              |

Edit a file in `web/`, refresh the browser. Pages are sent with a strict
Content-Security-Policy (scripts only from this server, no inline scripts).

## Organisers

Set `ADMIN_TOKEN` in `deploy/.env` to switch on the organiser API (team and access-code
management, company assignment, phases, time back, announcements, alerts, HR log, clues,
team view, fixes, live settings, content reload). See [ORGANISERS.md](ORGANISERS.md).

## Frontend

See [FRONTEND.md](FRONTEND.md): sessions, errors, interviews, evidence cards, the board,
and what the UI must never show.

## Deploying

See [DEPLOY.md](DEPLOY.md) for manual single-replica production deployment with Docker Compose and shared Caddy. Secrets are managed in `deploy/.env`; backups and Gemini failover are not configured.

## Status

See [BUILD_STATUS.md](BUILD_STATUS.md).
