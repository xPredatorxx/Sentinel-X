#!/bin/sh
# Sauvegarde périodique de PostgreSQL (MCO) : pg_dump compressé, rotation des anciennes copies.
set -eu
set -o pipefail
INTERVAL="${BACKUP_INTERVAL_S:-21600}"
KEEP="${BACKUP_KEEP:-28}"
export PGPASSWORD="$(cat /run/secrets/db_app_password)"
umask 077

until pg_isready -h db -U "$DB_USER" -d "$DB_NAME" -q; do sleep 5; done
while true; do
  f="/backups/sentinel-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  if pg_dump -h db -U "$DB_USER" -d "$DB_NAME" --no-owner --no-privileges --clean --if-exists | gzip -9 > "$f.tmp"; then
    mv "$f.tmp" "$f"
    date -u +%s > /backups/.last_success
    echo "[backup] OK $(basename "$f") ($(wc -c < "$f") octets)"
  else
    rm -f "$f.tmp"
    echo "[backup] ÉCHEC de la sauvegarde" >&2
  fi
  # rotation : on garde les KEEP sauvegardes les plus récentes
  ls -1t /backups/sentinel-*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
  sleep "$INTERVAL"
done
