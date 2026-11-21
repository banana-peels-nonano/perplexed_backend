#!/bin/sh
# Container entrypoint: migrate, (optionally) load the case, then serve on $PORT.
# SEED_CASE: a case file path inside the image/mount (e.g. content/case.toml). Seeding is
# idempotent: it upserts content and never touches teams, threads or evidence.
set -e
alembic upgrade head
if [ -n "${SEED_CASE:-}" ]; then
  python -m app.seed "$SEED_CASE" --event-name "${EVENT_NAME:-PERPLEXED 2.0}"
fi
# The app logs request outcomes and socket lifecycle events. Keep Uvicorn warnings and errors.
# ONE process on purpose: live updates keep their registry in memory (D-021).
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --no-access-log --log-level warning --proxy-headers --forwarded-allow-ips='*' +  --timeout-graceful-shutdown 10
