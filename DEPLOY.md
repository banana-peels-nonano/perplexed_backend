# Deploying PERPLEXED 2.0

One Linux server, Docker Compose: the app (API + frontend), Postgres, a backup job, and
Caddy for HTTPS. Everything is in `deploy/`.

## The event server (Saturnalia subdomain)

Before you start:
- SSH access to the server, with Docker and Docker Compose v2 installed.
- An A record for the subdomain pointing at the server's IP. If the domain is on
  Cloudflare, set it to "DNS only" (grey cloud) at least until the certificate is issued.
- The event Anthropic API key.

```bash
git clone https://github.com/banana-peels-nonano/perplexed_backend.git perplexed
cd perplexed
mkdir -p content && scp you@laptop:case.toml content/case.toml   # the real case: never in git
cd deploy && ./setup.sh
```

`setup.sh` asks for the domain and the API key, writes `deploy/.env` (mode 600) with a fresh
database password and organiser token, prints the token once, builds, starts, waits for
`/healthz`, and loads `content/case.toml` (or the placeholder if it isn't there yet). Then:

- `https://<domain>/` team portal
- `https://<domain>/console` organiser console (paste the token)
- `https://<domain>/display` projector

If ports 80/443 are already used by an nginx on the server, `setup.sh` starts without Caddy
and you add `deploy/nginx-perplexed.conf` to that nginx. Keep the two `Upgrade`/`Connection`
lines in it, or live updates never connect, and keep `client_max_body_size 25m` for uploads.

### Day-to-day

```bash
cd perplexed/deploy
docker compose -f compose.prod.yaml ps
docker compose -f compose.prod.yaml logs -f app
git pull && docker compose -f compose.prod.yaml --profile caddy up -d --build   # update
```

A new version of the case: copy it to `content/case.toml`, then console → Settings → Reload.

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

### Things that must stay true

- **One app process.** Live updates keep their registry in memory. Don't add workers or
  replicas.
- **One origin.** The frontend is served by the app, so `/` and `/api` share the domain and
  the session cookie (HttpOnly, SameSite=Strict, Secure) just works.
- `deploy/.env` and `content/` never go into git.

## Free staging on Render (optional, not for the event)

render.com → sign in with GitHub → New → Blueprint → this repo. `render.yaml` creates a
free web service and a free database, asks for the Anthropic key (use the dev key), and
generates an organiser token (Dashboard → the service → Environment). It loads the
placeholder case only. Free services sleep after 15 idle minutes and the free database is
deleted after 30 days, so this is for clicking around, not for running the event.
