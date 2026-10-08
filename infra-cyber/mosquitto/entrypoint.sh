#!/bin/sh
# Construit le fichier de mots de passe (hachés) à partir des secrets Docker,
# dans un tmpfs : rien n'est écrit sur le disque de l'hôte, rien en clair dans l'image.
set -eu
umask 077
DIR=/tmp/mosquitto
mkdir -p "$DIR"
: > "$DIR/passwd"

add_user() {
  [ -s "$2" ] || { echo "secret manquant : $2" >&2; exit 1; }
  printf '%s:%s\n' "$1" "$(cat "$2")" >> "$DIR/passwd"
}

add_user api "/run/secrets/mqtt_api_password"
add_user "${MQTT_EDGE_USER:?MQTT_EDGE_USER requis}" "/run/secrets/mqtt_edge_password"

# hachage en place (PBKDF2-SHA512) : le mot de passe ne passe jamais en argument
mosquitto_passwd -U "$DIR/passwd"
cp /mosquitto/config/acl "$DIR/acl"

exec mosquitto -c /mosquitto/config/mosquitto.conf
