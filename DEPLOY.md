# Deploying PERPLEXED 2.0

One Linux server, Docker Compose: the app (API + frontend), Postgres, a backup job, and
Caddy for HTTPS. Everything is in `deploy/`.

## First deploy: the server's IP, no domain yet

Plain http on `http://<server-ip>/`. Fine for setting up and testing; **not for the event**:
without HTTPS, access codes and the organiser token cross the network readable. Switch to
the subdomain (next section) before teams log in for real.

```bash
# 1. on your laptop: get in, check Docker
ssh <user>@<server-ip>
docker --version && docker compose version     # need Compose v2 ("docker compose", no dash)

# 2. the code (private repo: use a GitHub deploy key or a fine-grained token, read-only)
git clone https://github.com/banana-peels-nonano/perplexed_backend.git perplexed

# 3. the case (from your laptop, in another terminal; never in git)
ssh <user>@<server-ip> 'mkdir -p ~/perplexed/content'
scp content/case1_silent_bundle.toml <user>@<server-ip>:~/perplexed/content/case.toml

# 4. on the server
cd ~/perplexed/deploy && ./setup.sh
#    Domain: press Enter (empty = plain http on this IP)
#    Anthropic key: the event key
#    copy the organiser token it prints into your password manager
```

`setup.sh` writes `deploy/.env` with `DOMAIN=:80` and `COOKIE_SECURE=false`, starts Caddy on
port 80, the app, Postgres and backups, loads `content/case.toml`, and prints
`http://<ip>/`, `/console`, `/display`.

If the page doesn't load from your laptop, the server's firewall is closed: `sudo ufw allow
80/tcp` (and `443/tcp` for later), or open port 80 in the cloud provider's network rules.

**Email for registration**: edit `deploy/.env` (`nano deploy/.env`), fill `SMTP_HOST`,
`SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` (in quotes), and `PUBLIC_URL=http://<ip>/`, then
`docker compose -f compose.prod.yaml --profile caddy up -d` (recreates the app with the new
settings). Console → Registration stops saying "Email isn't set up".

**A different case, or a clean slate** (wipes teams too; never during the event):

```bash
docker compose -f compose.prod.yaml exec app python -m app.seed content/case.toml --fresh
```

**Switching to the subdomain** once DNS points at the server:

```bash
nano deploy/.env    # DOMAIN=perplexed.<their-domain>   COOKIE_SECURE=true   PUBLIC_URL=https://perplexed.<their-domain>/
docker compose -f compose.prod.yaml --profile caddy up -d
```

Caddy fetches the certificate by itself on the first visit (ports 80 and 443 must be open).
Nothing in the database changes; teams and codes carry over.

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
