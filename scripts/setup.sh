#!/usr/bin/env bash
# SENTINEL-X — préparation du PC serveur (à lancer UNE fois, depuis la racine du dépôt, dans Git Bash ou Linux)
#
#   bash scripts/setup.sh                         # IP par défaut : 192.168.137.10
#   bash scripts/setup.sh 192.168.137.10 192.168.10.10  # IP/noms du serveur à inscrire dans les certificats
#   bash scripts/setup.sh --renew-certs 192.168.137.10   # régénère les certificats serveur (la CA est conservée)
#   WIFI_PASSWORD=xxxx bash scripts/setup.sh 192.168.137.10  # mot de passe du point d'accès écrit dans secrets.h
#
# Génère (sans jamais rien écraser d'existant, sauf --renew-certs) :
#   .env                         configuration non secrète
#   secrets/*                    mots de passe et clés aléatoires (Docker secrets)
#   infra-cyber/certs/           CA interne + certificats HTTPS (Nginx) et MQTTS (Mosquitto)
#   edge-firmware/include/secrets.h   configuration du boîtier (CA, mot de passe MQTT)
# Tous ces fichiers sont ignorés par Git.
set -euo pipefail
export MSYS_NO_PATHCONV=1      # Git Bash : ne pas transformer "/CN=..." en chemin Windows

cd "$(dirname "$0")/.."
ROOT=$(pwd)
RENEW=0
if [[ "${1:-}" == "--renew-certs" ]]; then RENEW=1; shift; fi
HOSTS=("$@")
[[ ${#HOSTS[@]} -eq 0 ]] && HOSTS=(192.168.137.10)

command -v openssl >/dev/null || { echo "openssl introuvable (installez Git for Windows)"; exit 1; }

rand() { openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-"$1"; }

# --- 1. .env ------------------------------------------------------------------------
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[ok] .env créé depuis .env.example"
fi
NODE_ID=$(grep -E '^NODE_ID=' .env | cut -d= -f2- || true)
NODE_ID=${NODE_ID:-sentinel-edge-01}

# --- 2. Secrets ---------------------------------------------------------------------------
mkdir -p secrets
chmod 700 secrets
NEW_ACCOUNTS=0
for name in db_root_password db_app_password mqtt_api_password mqtt_edge_password ai_api_key camera_token jwt_secret admin_password viewer_password; do
  f="secrets/$name"
  if [[ ! -s "$f" ]]; then
    case $name in
      jwt_secret) rand 64 > "$f" ;;
      admin_password|viewer_password) rand 20 > "$f"; NEW_ACCOUNTS=1 ;;
      *) rand 40 > "$f" ;;
    esac
    # lisible par l'utilisateur du conteneur ; le dossier (700) protège des autres comptes de l'hôte
    chmod 644 "$f"
    echo "[ok] secret généré : $f"
  fi
done

# --- 3. Certificats (CA interne ECDSA P-256) -------------------------------------------------
CERTS=infra-cyber/certs
mkdir -p "$CERTS"
chmod 700 "$CERTS"

if [[ ! -f $CERTS/ca.key ]]; then
  openssl ecparam -name prime256v1 -genkey -noout -out $CERTS/ca.key
  openssl req -x509 -new -key $CERTS/ca.key -sha256 -days 3650 \
    -subj "/O=AetherCorp/CN=Sentinel-X Internal CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -out $CERTS/ca.crt
  echo "[ok] CA interne créée (10 ans) — elle n'est jamais régénérée, inutile de reflasher les boîtiers"
fi

# SAN : chaque IP est inscrite en IP: ET en DNS: (le mbedTLS du core ESP32 ne compare
# le nom d'hôte qu'aux entrées DNS/CN, pas aux SAN de type IP).
san_for() {
  local extra=$1 san="DNS:localhost,IP:127.0.0.1"
  for h in "${HOSTS[@]}"; do
    if [[ $h =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then san+=",IP:$h,DNS:$h"; else san+=",DNS:$h"; fi
  done
  [[ -n $extra ]] && san+=",$extra"
  echo "$san"
}

issue() {   # issue <nom> <CN> <SAN supplémentaires>
  local name=$1 cn=$2 extra=$3
  if [[ -f $CERTS/$name.crt && $RENEW -eq 0 ]]; then return; fi
  openssl ecparam -name prime256v1 -genkey -noout -out $CERTS/$name.key
  openssl req -new -key $CERTS/$name.key -subj "/O=AetherCorp/CN=$cn" -out $CERTS/$name.csr
  cat > $CERTS/$name.ext <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=$(san_for "$extra")
EOF
  openssl x509 -req -in $CERTS/$name.csr -CA $CERTS/ca.crt -CAkey $CERTS/ca.key -CAcreateserial \
    -days 825 -sha256 -extfile $CERTS/$name.ext -out $CERTS/$name.crt 2>/dev/null
  rm -f $CERTS/$name.csr $CERTS/$name.ext
  echo "[ok] certificat $name : $(san_for "$extra")"
}

issue server "${HOSTS[0]}" ""        # HTTPS (Nginx)
issue mqtt   "${HOSTS[0]}" "DNS:mqtt" # MQTTS (Mosquitto) ; "mqtt" = nom du service pour l'API
chmod 644 $CERTS/*.crt $CERTS/server.key $CERTS/mqtt.key
chmod 600 $CERTS/ca.key            # la clé de la CA ne sort jamais de l'hôte (aucun conteneur ne la monte)

# --- 4. Configuration du boîtier ----------------------------------------------------------------
FW=edge-firmware/include/secrets.h
if [[ ! -f $FW ]]; then
  {
    echo "// Généré par scripts/setup.sh — NE PAS COMMITER (ignoré par Git)"
    echo "#pragma once"
    echo ""
    echo "#define WIFI_SSID     \"${WIFI_SSID:-SENTINEL-X}\""
    echo "#define WIFI_PASSWORD \"${WIFI_PASSWORD:-CHANGE_ME}\""
    echo ""
    echo "#define MQTT_HOST \"${HOSTS[0]}\""
    echo "#define MQTT_PORT 8883"
    echo ""
    echo "// adresse fixe du boîtier sur le réseau de table (supprimer ces 2 lignes pour utiliser DHCP)"
    echo "#define ESP_STATIC_IP \"${ESP_STATIC_IP:-192.168.137.20}\""
    echo "#define ESP_GATEWAY   \"${ESP_GATEWAY:-192.168.137.1}\""
    echo ""
    echo "#define NODE_ID   \"$NODE_ID\""
    echo "#define MQTT_PASS \"$(cat secrets/mqtt_edge_password)\""
    echo ""
    echo "static const char MQTT_CA_CERT[] = R\"EOF("
    cat $CERTS/ca.crt
    echo ")EOF\";"
  } > $FW
  echo "[ok] $FW généré — vérifiez WIFI_PASSWORD (ou relancez avec WIFI_PASSWORD=... devant la commande) puis flashez le boîtier"
fi

# --- 5. Dossier de sauvegardes ------------------------------------------------------------------
mkdir -p backups

echo ""
echo "Préparation terminée."
if [[ $NEW_ACCOUNTS -eq 1 ]]; then
  echo "Comptes du dashboard (notez-les, ils sont aussi dans secrets/) :"
  echo "  $(grep -E '^ADMIN_USERNAME=' .env | cut -d= -f2- || echo admin)     / $(cat secrets/admin_password)   (administrateur)"
  echo "  $(grep -E '^VIEWER_USERNAME=' .env | cut -d= -f2- || echo operateur) / $(cat secrets/viewer_password)   (lecture seule)"
fi
echo "Étape suivante :  docker compose up -d --build   puis  https://${HOSTS[0]}"
echo "Importez infra-cyber/certs/ca.crt dans le navigateur pour éviter l'avertissement de certificat."
