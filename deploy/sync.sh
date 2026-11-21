#!/bin/sh
# Publish the local project working tree to Tevaj. Preview by default.
set -eu

usage() {
    printf 'Usage: %s [--dry-run | --apply | --inspect]\n' "$0"
}

if [ "$#" -gt 1 ]; then usage >&2; exit 2; fi
case "${1:---dry-run}" in
    --dry-run) set -- --dry-run ;;
    --apply) set -- ;;
    --inspect)
        exec ssh -o ConnectTimeout=10 -o ControlPath=~/.ssh/perplexed-Tevaj.sock Tevaj 'sh -s' <<'REMOTE'
set -eu
cd /opt/perplexed
printf 'Deployment directory: %s\n' "$(pwd -P)"
ls -ld . deploy
for path in deploy/.env content .git compose.yaml compose.prod.yaml deploy/compose.prod.yaml; do
    if [ -e "$path" ]; then printf 'Present: %s\n' "$path"; fi
done
if command -v rsync >/dev/null; then rsync --version | head -n 1; fi
if [ -f deploy/compose.prod.yaml ] && [ -f deploy/.env ]; then
    cd deploy
    printf '\nCompose services\n'
    docker compose --env-file .env -f compose.prod.yaml config --services
    printf '\nCompose volumes\n'
    docker compose --env-file .env -f compose.prod.yaml config --volumes
    printf '\nContainers\n'
    docker compose --env-file .env -f compose.prod.yaml ps
fi
REMOTE
        ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac

if ! command -v rsync >/dev/null; then
    printf '%s\n' 'rsync is required on this machine and on Tevaj.' >&2
    exit 1
fi

cd "$(dirname "$0")/.."
manifest=$(mktemp)
trap 'rm -f "$manifest"' EXIT
trap 'exit 1' HUP INT TERM
git ls-files -z --cached --others --exclude-standard > "$manifest"

# Copy the working tree bytes for every listed path. Keep files managed on Tevaj and
# compare contents since the checkout and server may have different timestamps.
rsync -lR --checksum --safe-links --itemize-changes --stats \
    --from0 --files-from="$manifest" --ignore-missing-args \
    -e 'ssh -o ConnectTimeout=10 -o ControlPath=~/.ssh/perplexed-Tevaj.sock' \
    --rsync-path='cd /opt/perplexed && test -d deploy && test ! -L deploy && test -f deploy/.env && exec rsync' \
    --include='.env.example' --exclude='.env' --exclude='.env.*' \
    --exclude='.git' --exclude='.secrets' \
    --exclude='/.aws/' --exclude='/.agents/' --exclude='/.codex/' \
    --exclude='/content/' --exclude='/deploy/backups/' \
    "$@" -- ./ Tevaj:/opt/perplexed/
