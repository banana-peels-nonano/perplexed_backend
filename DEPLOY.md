# Deploying PERPLEXED 2.0

Production is deployed manually with Docker Compose as one app replica and one
PostgreSQL service. The server's shared Caddy serves `perplexed.saturnalia.in` over HTTPS
on the external Docker network `web`. Keep secrets in `deploy/.env`. Database backups are
not configured; PostgreSQL data persists in the named Docker volume only.

## First deploy

Clone the repository on the server and put the event case at `content/case.toml`.
The app container runs as UID 10001 and needs read access to the file. Keep the
case and `deploy/.env` out of git.

From the repository root:

```bash
cd deploy
(umask 077 && cp -n env.prod.example .env)
chmod 600 .env
```

Set `POSTGRES_PASSWORD`, `ADMIN_TOKEN`, and `ANTHROPIC_API_KEY` in `.env`. For a new
database, `openssl rand -hex 24` generates a suitable database password; generate a
separate strong organiser token. For an existing database, keep its current password.
Gemini is not used for the event. Compose gives exported shell variables
precedence over `.env`, so keep deployment settings in the file and unset any
conflicting exports before running Compose.

Point DNS at the server and add this block to the shared Caddyfile, then reload
the shared Caddy configuration:

```caddyfile
perplexed.saturnalia.in {
    reverse_proxy perplexed-api:8000
}
```

Run the deployment from `deploy/`:

```bash
docker compose --env-file .env -f compose.prod.yaml up -d --build --wait
```

The app runs migrations on startup. To load the event case on a fresh database
with `SEED_CASE` left blank, run:

```bash
docker compose --env-file .env -f compose.prod.yaml exec -T app python -m app.seed content/case.toml
```

Check `https://perplexed.saturnalia.in/healthz`, then open `/console` with the
organiser token.

## Manual deploy and redeploy

From your local checkout, use the SSH alias `Tevaj` to inspect and sync the project files
to `/opt/perplexed`. Install `rsync` on both machines before syncing.

```bash
sh deploy/sync.sh --inspect
sh deploy/sync.sh
sh deploy/sync.sh --apply
```

The middle command is a dry run. The script copies tracked files and new files that Git
doesn't ignore, using their current working tree contents. This includes docs, tests,
dotfiles and local changes. It preserves server-only files, including `deploy/.env` and
case data. Git metadata stays local, and the Docker volumes are outside the sync.

After the sync, SSH in and rebuild manually:

```bash
ssh Tevaj
cd /opt/perplexed/deploy
docker compose --env-file .env -f compose.prod.yaml down
docker compose --env-file .env -f compose.prod.yaml up -d --build --wait
```

Keep one app replica because live updates use an in-memory registry. The container runs
database migrations on startup. To update case content, replace `content/case.toml` and
reload it from the organiser console.

The Compose deployment does not include a backup or restore workflow. The named database
volume survives ordinary container recreation, but it is not a backup.

## Application logs

From `deploy/`, follow the app logs with:

```bash
docker compose --env-file .env -f compose.prod.yaml logs --no-log-prefix -f app
```

Application events are one JSON object per line on stdout. Filter by `event`, `level`,
`route`, or `request_id`. API responses and `/healthz` return a server-generated
`X-Request-ID` that ties an HTTP request to its interview or notification events. Organiser
writes use `admin.write` and include the affected team UUID on individual team controls.
The shared organiser token identifies the actor as an admin, so these logs cannot
distinguish individual organisers. Uvicorn and migration messages are separate.

Set `LOG_LEVEL` and `LOG_SLOW_MS` in `deploy/.env` and rerun Compose to apply changes.
The default is `INFO` with a 1,000 ms threshold for slow reads. Routine polling and
health checks appear at `DEBUG`, capped at 120 read entries per minute. Warnings
and errors have separate per-event caps of 30 or 60 entries per minute. When the next
window has activity, `logs.suppressed` reports how many entries were skipped. Docker
rotates logs at five 10 MB files per container. Per-message WebSocket traffic, request
bodies, query strings, client IPs, credentials and interview text are excluded from the
application events.

## Free staging on Render (optional, not for the event)

render.com → sign in with GitHub → New → Blueprint → this repo. `render.yaml` creates a
free web service and a free database, asks for the Anthropic key (use the dev key), and
generates an organiser token (Dashboard → the service → Environment). It loads the
placeholder case only. Free services sleep after 15 idle minutes and the free database is
deleted after 30 days, so this is for clicking around, not for running the event.
