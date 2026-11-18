#!/usr/bin/env bash
# One-time setup on the event server. Run from the repo:  cd deploy && ./setup.sh
# Re-running is safe: it keeps an existing deploy/.env and just rebuilds and restarts.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing: $1"; exit 1; }; }
need docker
docker compose version >/dev/null || { echo "docker compose v2 is required"; exit 1; }
DC="docker compose -f compose.prod.yaml"
if ! docker info >/dev/null 2>&1; then DC="sudo $DC"; fi

if [[ ! -f .env ]]; then
  say "Creating deploy/.env"
  read -rp "Domain (e.g. perplexed.example.org; leave EMPTY to serve plain http on this server's IP for now): " domain
  read -rsp "Anthropic API key (event key): " akey; echo
  pg=$(openssl rand -hex 24)
  admin=$(openssl rand -base64 36 | tr -d '/+=' | cut -c1-43)
  secure=true
  if [[ -z "$domain" ]]; then domain=":80"; secure=false; fi  # IP only: http, no certificate
  umask 077
  cat > .env <<ENV
DOMAIN=$domain
POSTGRES_PASSWORD=$pg
ADMIN_TOKEN=$admin
ANTHROPIC_API_KEY=$akey
GEMINI_API_KEY=
COOKIE_SECURE=$secure
BACKUP_EVERY_SECONDS=300
BACKUP_KEEP=288
# email for team registration (access codes). Fill in, then: docker compose ... up -d
# Gmail: smtp.gmail.com / 587 / the address / a 16-character app password
# SMTP_FROM needs quotes: SMTP_FROM="PERPLEXED 2.0 <someone@gmail.com>"
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
PUBLIC_URL=
ENV
  chmod 600 .env
  say "Organiser token (also in deploy/.env; paste it into /console):"
  echo "$admin"
fi
# read only what this script needs (don't `source` .env: values like SMTP_FROM have spaces)
env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/'; }
DOMAIN=$(env_get DOMAIN); BACKUP_EVERY_SECONDS=$(env_get BACKUP_EVERY_SECONDS)
mkdir -p backups ../content

PROFILE="--profile caddy"
if ! $DC ps --status running 2>/dev/null | grep -q caddy && ss -ltn 2>/dev/null | grep -qE ':(80|443)\s'; then
  say "Ports 80/443 are already taken on this server (an existing nginx?)."
  echo "Starting WITHOUT Caddy. Point your proxy at 127.0.0.1:8000 (see deploy/nginx-perplexed.conf)."
  PROFILE=""
fi

say "Building and starting"
$DC $PROFILE up -d --build

say "Waiting for the app"
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS http://127.0.0.1:8000/healthz && echo

if ! $DC exec -T db psql -U perplexed -tAc "select 1 from event limit 1" perplexed 2>/dev/null | grep -q 1; then
  case_file=../content/case.toml
  [[ -f $case_file ]] || case_file=../tests/fixtures/northfield.toml
  say "Seeding the case from ${case_file#../} (no teams: register them in /console)"
  $DC exec -T app python -m app.seed "${case_file#../}" --event-name "PERPLEXED 2.0"
fi
# later case updates: put the file in content/ and use /console → Settings → Reload the case

say "Done."
if [[ -z "$PROFILE" ]]; then echo "  the app is on 127.0.0.1:8000 only; put your proxy in front (deploy/nginx-perplexed.conf)"
elif [[ "$DOMAIN" == ":80" ]]; then
  ip=$(curl -fsS -4 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
  echo "  http://$ip/          team portal      (plain http: for testing until the domain exists)"
  echo "  http://$ip/console   organisers"; echo "  http://$ip/display   projector"
else echo "  https://$DOMAIN/          team portal"; echo "  https://$DOMAIN/console   organisers"; echo "  https://$DOMAIN/display   projector"; fi
echo "  backups: deploy/backups/ every ${BACKUP_EVERY_SECONDS:-300}s"
