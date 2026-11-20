# Deploying PERPLEXED 2.0

The production Compose project runs the app, Postgres, and backups. The server's
shared Caddy serves `perplexed.saturnalia.in` over HTTPS on the external Docker
network `web`.

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

Set `POSTGRES_PASSWORD` and `ADMIN_TOKEN` in `.env`. For a new database,
`openssl rand -hex 24` generates a suitable value for each. For an existing
database, use its current password. Set the event API key and fill in the SMTP
settings if teams will register by email. Compose gives exported shell variables
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

## Manual redeploy

Pull the desired revision and run the same `up` command from `deploy/`. Keep a
single app process because live updates use an in-memory registry. To update
case content, replace `content/case.toml` and reload it from the organiser console.

### Backups

`deploy/backups/` gets a compressed dump every 5 minutes; the newest 288 (24 hours) are kept.
Copy one off the server now and then (`scp`). To restore one:

```bash
docker compose -f compose.prod.yaml stop app
gunzip -c backups/perplexed-<stamp>.sql.gz | docker compose -f compose.prod.yaml exec -T db psql -U perplexed perplexed
docker compose -f compose.prod.yaml start app
```

(Restore into an empty database: `docker compose ... down`, remove the `perplexed_pgdata`
volume, `up -d db`, then the `gunzip | psql` line.)

## Free staging on Render (optional, not for the event)

render.com → sign in with GitHub → New → Blueprint → this repo. `render.yaml` creates a
free web service and a free database, asks for the Anthropic key (use the dev key), and
generates an organiser token (Dashboard → the service → Environment). It loads the
placeholder case only. Free services sleep after 15 idle minutes and the free database is
deleted after 30 days, so this is for clicking around, not for running the event.
