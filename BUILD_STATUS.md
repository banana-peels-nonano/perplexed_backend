# PERPLEXED 2.0 — Engineering Status

Last updated: 14 November 2026

## 1. Project objective

Build the backend for PERPLEXED 2.0, supporting team investigation,
AI stakeholder interviews, company records, alliances, submissions,
judging and event administration.

Hard deadline: Thursday, 19 November 2026.

## 2. Environment

- OS: Ubuntu 24.04.5 LTS
- Python: 3.12.3
- Git: 2.43.0
- Docker: 29.9.2
- Docker Compose: 5.6.1
- Docker access: sudo; no Docker-group membership

## 3. Architecture decisions

- Modular monolith using FastAPI.
- PostgreSQL as the primary database.
- pgvector for vector similarity search.
- SQLAlchemy for database access.
- Alembic for schema migrations.
- Docker Compose for local infrastructure.
- Separate backend repository from the existing frontend.
- LLM provider will be selected after benchmarking Claude and Gemini.

## 4. Security principles

- Server-side company isolation.
- Hidden trust and unrevealed clues never exposed to participants.
- Secrets excluded from Git.
- Local database bound to loopback only.
- No Docker-group membership.
- Persistent audit trail for competition actions.

## 5. Event-rule questions to resolve

- Confirm two-round versus four-round event format.
- Confirm final phase names and durations.
- Confirm team-size policy.
- Confirm three-team alliance scoring.
- Confirm question quotas.
- Confirm tie-breaker implementation.
- Confirm time-add-back procedure.

## 6. Implementation progress

| Component | Status |
|---|---|
| Ubuntu development environment | Verified |
| Docker Engine | Verified |
| Python virtual environment | Verified |
| Backend repository | In progress |
| PostgreSQL + pgvector | Pending |
| FastAPI foundation | Pending |
| Authentication and teams | Pending |
| Event engine | Pending |
| AI engine and retrieval | Pending |
| Trust and HR mechanics | Pending |
| Records and Evidence Board | Pending |
| Alliances | Pending |
| Submissions and judging | Pending |
| Admin and audit logging | Pending |
| Frontend integration | Pending |
| Staging and production deployment | Pending |

## 7. Current blockers

- Event-format clarifications.
- Actual stakeholder, clue and company-record content.
- Final hosting/deployment target.
- Provider quota and credit verification.
