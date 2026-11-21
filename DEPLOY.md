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
SMTP and Gemini are not used for the event. Compose gives exported shell variables
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

Pull the desired revision and run the same `up` command from `deploy/`. Do not scale the
app service: live updates use an in-memory registry and the deployment is single replica.
The container runs database migrations on startup. To update case content, replace
`content/case.toml` and reload it from the organiser console.

The Compose deployment does not include a backup or restore workflow. The named database
volume survives ordinary container recreation, but it is not a backup.

## Free staging on Render (optional, not for the event)

render.com → sign in with GitHub → New → Blueprint → this repo. `render.yaml` creates a
free web service and a free database, asks for the Anthropic key (use the dev key), and
generates an organiser token (Dashboard → the service → Environment). It loads the
placeholder case only. Free services sleep after 15 idle minutes and the free database is
deleted after 30 days, so this is for clicking around, not for running the event.
