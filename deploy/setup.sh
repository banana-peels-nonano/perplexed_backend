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
  read -rp "Domain (e.g. perplexed.saturnalia.in; empty = plain http on this machine's IP): " domain
  read -rsp "Anthropic API key (event key): " akey; echo
  pg=$(openssl rand -hex 24)
  admin=$(openssl rand -base64 36 | tr -d '/+=' | cut -c1-43)
  secure=true; [[ -z "$domain" ]] && secure=false
  umask 077
  cat > .env <<ENV
DOMAIN=${domain:-localhost}
POSTGRES_PASSWORD=$pg
ADMIN_TOKEN=$admin
ANTHROPIC_API_KEY=$akey
GEMINI_API_KEY=
COOKIE_SECURE=$secure
BACKUP_EVERY_SECONDS=300
BACKUP_KEEP=288
ENV
  chmod 600 .env
  say "Organiser token (also in deploy/.env; paste it into /console):"
  echo "$admin"
fi
set -a; . ./.env; set +a
mkdir -p backups ../content

PROFILE=""
if [[ "${DOMAIN:-localhost}" != "localhost" ]]; then
  if ss -ltn 2>/dev/null | grep -qE ':(80|443)\s'; then
    say "Ports 80/443 are already taken on this server (an existing nginx?)."
    echo "Starting WITHOUT Caddy. Point your proxy at 127.0.0.1:8000 (see deploy/nginx-perplexed.conf)."
  else
    PROFILE="--profile caddy"
  fi
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
if [[ -n "$PROFILE" ]]; then echo "  https://$DOMAIN/          team portal"; echo "  https://$DOMAIN/console   organisers"; echo "  https://$DOMAIN/display   projector"
else echo "  http://<server-ip>:8000 is loopback-only; put a proxy in front (deploy/nginx-perplexed.conf)"; fi
echo "  backups: deploy/backups/ every ${BACKUP_EVERY_SECONDS:-300}s"
