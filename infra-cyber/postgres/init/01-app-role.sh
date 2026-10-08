#!/bin/sh
# Exécuté une seule fois, à la création de la base (volume vide).
# L'API utilise un rôle dédié SANS privilèges d'administration (moindre privilège) :
# pas superuser, ne peut créer ni base ni rôle, n'accède qu'à la base sentinel.
set -eu
APP_PW="$(cat /run/secrets/db_app_password)"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_user="$APP_DB_USER" -v app_pw="$APP_PW" -v db_name="$POSTGRES_DB" <<'EOSQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db_name" TO :"app_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
EOSQL
echo "Rôle applicatif $APP_DB_USER créé"
