#!/bin/sh
# A database dump every $EVERY_SECONDS, keeping the newest $KEEP. Runs in the backup container.
# Restore: gunzip -c backups/perplexed-XXXX.sql.gz | docker compose -f compose.prod.yaml exec -T db psql -U perplexed perplexed
set -u
mkdir -p /backups
while true; do
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  if pg_dump -h db -U perplexed --no-owner perplexed | gzip > "/backups/.tmp.$stamp.sql.gz"; then
    mv "/backups/.tmp.$stamp.sql.gz" "/backups/perplexed-$stamp.sql.gz"
  else
    rm -f "/backups/.tmp.$stamp.sql.gz"; echo "backup failed at $stamp" >&2
  fi
  ls -1t /backups/perplexed-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
  sleep "$EVERY_SECONDS"
done
