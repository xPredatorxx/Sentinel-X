#!/bin/sh
# Restauration : docker compose exec backup sh /scripts/restore.sh sentinel-AAAAMMJJTHHMMSSZ.sql.gz
set -eu
[ $# -eq 1 ] || { echo "usage: restore.sh <fichier.sql.gz>"; exit 1; }
case "$1" in */*|..*) echo "nom de fichier invalide"; exit 1;; esac
export PGPASSWORD="$(cat /run/secrets/db_app_password)"
gunzip -c "/backups/$1" | psql -v ON_ERROR_STOP=1 -h db -U "$DB_USER" -d "$DB_NAME"
echo "Restauration terminée"
