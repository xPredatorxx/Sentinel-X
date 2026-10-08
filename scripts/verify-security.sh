#!/usr/bin/env bash
# SENTINEL-X — vérification automatique du durcissement (à lancer après docker compose up -d)
#   bash scripts/verify-security.sh [hôte]        (hôte par défaut : localhost)
# Chaque ligne affiche OK / ÉCHEC : preuve reproductible pour la soutenance.
set -uo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
# hôte par défaut : première IPv4 du réseau local (le relais loopback de WSL ne sert pas le port 80)
default_ip() {
  { hostname -I 2>/dev/null; ipconfig 2>/dev/null | grep -i ipv4; } \
    | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | grep -vE '^(127|255|0)\.' | head -1
}
HOST=${1:-$(default_ip)}
HOST=${HOST:-127.0.0.1}
echo "Hôte testé : $HOST"
curl() { command curl --noproxy "*" -m 10 "$@"; }
PASS=0; FAIL=0
ok()   { echo "  [OK]    $1"; PASS=$((PASS + 1)); }
ko()   { echo "  [ÉCHEC] $1"; FAIL=$((FAIL + 1)); }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else ko "$1"; fi; }
port_open() { timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

NODE_ID=$(grep -E '^NODE_ID=' .env 2>/dev/null | cut -d= -f2-); NODE_ID=${NODE_ID:-sentinel-edge-01}
mq() { docker compose exec -T mqtt "$@"; }
MQ_OPTS=(-h localhost -p 8883 --cafile /run/certs/ca.crt -i "verify-$$")

echo "== 1. Conteneurs"
for svc in db mqtt api ai web backup; do
  cid=$(docker compose ps -q "$svc" 2>/dev/null)
  if [[ -z $cid ]]; then ko "$svc : conteneur absent"; continue; fi
  read -r user priv ro nnp caps <<<"$(docker inspect -f '{{.Config.User}} {{.HostConfig.Privileged}} {{.HostConfig.ReadonlyRootfs}} {{range .HostConfig.SecurityOpt}}{{.}}{{end}} {{.HostConfig.CapDrop}}' "$cid")"
  [[ -n $user && $user != root && $user != 0 && $user != "0:0" ]] && ok "$svc : utilisateur non-root ($user)" || ko "$svc : tourne en root"
  [[ $priv == false ]] && ok "$svc : non privilégié" || ko "$svc : privilégié"
  [[ $ro == true ]] && ok "$svc : système de fichiers en lecture seule" || ko "$svc : FS inscriptible"
  [[ $nnp == *no-new-privileges* ]] && ok "$svc : no-new-privileges" || ko "$svc : no-new-privileges absent"
  [[ $caps == *ALL* ]] && ok "$svc : toutes les capabilities retirées" || ko "$svc : capabilities présentes"
  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")
  [[ $health == healthy || $health == none ]] && ok "$svc : état $health" || ko "$svc : état $health"
done
dev=$(docker inspect -f '{{len .HostConfig.Devices}}' "$(docker compose ps -q ai)" 2>/dev/null)
[[ ${dev:-0} -le 1 ]] && ok "ai : au plus 1 périphérique monté ($dev)" || ko "ai : $dev périphériques montés"
check "aucun conteneur ne monte le socket Docker" "! docker compose ps -q | xargs docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' | grep -q docker.sock"

echo "== 2. Réseau"
net=$(docker network ls --filter name=_backend -q | head -1)
[[ $(docker network inspect -f '{{.Internal}}' "$net") == true ]] && ok "réseau backend interne (pas de sortie Internet)" || ko "réseau backend non interne"
check "l'API ne peut pas joindre Internet" "! docker compose exec -T api python -c \"import socket; socket.create_connection(('1.1.1.1', 443), 3)\""
for p in 5432 8000 5000 1883 8081; do
  port_open "$HOST" $p && ko "port $p ouvert sur l'hôte" || ok "port $p fermé sur l'hôte"
done
published=""
for s in $(docker compose ps --services | sort); do
  b=$(docker inspect -f '{{range $p, $v := .HostConfig.PortBindings}}{{$p}} {{end}}' "$(docker compose ps -q "$s")")
  [[ -n ${b// /} ]] && published+="$s "
done
[[ $published == "mqtt web " ]] && ok "seuls mqtt et web publient des ports ($published)" || ko "services publiant des ports : $published"

echo "== 2b. Pont caméra (webcam USB externe)"
listen=$( (netstat -ano 2>/dev/null || ss -ltn 2>/dev/null) | grep -E "[:.]8554 .*(LISTEN|ÉCOUTE)" )
if [[ -z $listen ]]; then ko "pont caméra non démarré (scripts/start-camera-bridge.ps1)"
else
  grep -qvE "127.0.0.1:8554" <<<"$listen" && ko "pont caméra exposé hors de 127.0.0.1" || ok "pont caméra limité à 127.0.0.1"
  [[ $(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8554/stream) == 401 ]] && ok "pont caméra : jeton exigé (401 sans jeton)" || ko "pont caméra accessible sans jeton"
  [[ $(curl -s -o /dev/null -w "%{http_code}" -X POST -H "X-Camera-Token: $(cat secrets/camera_token)" http://127.0.0.1:8554/stream) == 405 ]] && ok "pont caméra en lecture seule (POST 405)" || ko "pont caméra accepte des écritures"
  ai_cam=$(docker compose exec -T ai python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://127.0.0.1:5000/health'))['camera'])" 2>/dev/null)
  [[ $ai_cam == True ]] && ok "IA : flux de la webcam externe reçu via le relais interne" || ko "IA sans image (camera=$ai_cam)"
fi

echo "== 3. HTTPS / Nginx"
code=$(curl -s -o /dev/null -w '%{http_code}' "http://$HOST/")
[[ $code == 301 ]] && ok "HTTP redirigé vers HTTPS (301)" || ko "HTTP → $code"
hdrs=$(curl -sk -D - -o /dev/null "https://$HOST/")
for h in strict-transport-security content-security-policy x-content-type-options x-frame-options referrer-policy permissions-policy; do
  grep -qi "^$h:" <<<"$hdrs" && ok "en-tête $h" || ko "en-tête $h absent"
done
grep -qi '^server: nginx/' <<<"$hdrs" && ko "version de Nginx exposée" || ok "version de Nginx masquée"
check "TLS 1.1 refusé" "! echo | openssl s_client -connect $HOST:443 -tls1_1"
check "TLS 1.2 accepté" "echo | openssl s_client -connect $HOST:443 -tls1_2 -CAfile infra-cyber/certs/ca.crt"
check "certificat HTTPS signé par la CA interne" "echo | openssl s_client -connect $HOST:443 -servername $HOST -CAfile infra-cyber/certs/ca.crt 2>/dev/null | grep -q 'Verify return code: 0'"

echo "== 4. API"
api() { curl -sk -o /dev/null -w '%{http_code}' "$@"; }
[[ $(api "https://$HOST/api/v1/alerts") == 401 ]] && ok "lecture des alertes sans session : 401" || ko "alertes lisibles sans session"
[[ $(api -X POST -H 'Content-Type: application/json' -d '{"node_id":"sentinel-vision-01","alert_type":"INTRUDER","severity":"CRITICAL"}' "https://$HOST/api/v1/alerts") == 401 ]] \
  && ok "création d'alerte sans authentification : 401" || ko "alerte acceptée sans authentification"
[[ $(api -X POST -H 'Content-Type: application/json' -H "X-API-Key: $(cat secrets/ai_api_key)" -d '{"node_id":"sentinel-vision-01","alert_type":"INTRUDER","severity":"CRITICAL"}' "https://$HOST/api/v1/alerts") == 401 ]] \
  && ok "clé API de l'IA inutilisable depuis l'extérieur (Nginx la supprime)" || ko "clé API acceptée via Nginx"
[[ $(api -X POST -H 'Content-Type: application/json' -d '{"username":"admin","password":"x"}' "https://$HOST/api/v1/auth/login") == 401 ]] && ok "mauvais mot de passe : 401" || ko "login inattendu"
check "Swagger désactivé" "! curl -sk https://$HOST/docs | grep -qi swagger"
[[ $(api "https://$HOST/video_feed") == 401 ]] && ok "flux vidéo protégé par la session" || ko "flux vidéo accessible sans session"
[[ $(api "https://$HOST/.env") == 403 ]] && ok "fichiers cachés refusés" || ko "fichiers cachés servis"
echo "== 5. MQTT (TLS + ACL, preuves de bout en bout)"
# Mosquitto accepte les abonnements puis applique l'ACL à la LIVRAISON de chaque message :
# on prouve donc les droits en observant ce qui arrive réellement (compteurs de l'API, réception).
# NB : le broker force l'identifiant client au nom d'utilisateur, ces tests déconnectent
# brièvement le vrai boîtier (il se reconnecte seul en 5 s).
EDGE_PW=$(cat secrets/mqtt_edge_password)
ADMIN_USER=$(grep -E '^ADMIN_USERNAME=' .env | cut -d= -f2-); ADMIN_USER=${ADMIN_USER:-admin}
JAR=$(mktemp)
command -v cygpath >/dev/null && JAR=$(cygpath -m "$JAR")   # curl Windows : chemin natif
for attempt in 1 2 3 4 5 6 7; do      # 429 possible si le script vient d'être lancé (limite 5 connexions/min)
  code=$(curl -sk -c "$JAR" -o /dev/null -w '%{http_code}' -H "Origin: https://$HOST" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$(cat secrets/admin_password)\"}" "https://$HOST/api/v1/auth/login")
  [[ $code == 429 ]] || break
  echo "  (limite de connexion atteinte, nouvel essai dans 15 s)"; sleep 15
done
[[ $code == 200 ]] && ok "connexion administrateur (session pour les tests suivants)" || ko "connexion administrateur impossible (HTTP $code)"
received() { curl -sk -b "$JAR" "https://$HOST/api/v1/system/status" | grep -oE '"mqtt_received":[0-9]+' | cut -d: -f2; }
EDGE=(-u "$NODE_ID" -P "$EDGE_PW")

check "connexion anonyme refusée" "! mq mosquitto_sub ${MQ_OPTS[*]} -t 'sentinel/#' -C 1 -W 3"
check "mauvais mot de passe refusé" "! mq mosquitto_sub ${MQ_OPTS[*]} -u $NODE_ID -P wrong -t 'sentinel/#' -C 1 -W 3"

r0=$(received)
mq mosquitto_pub "${MQ_OPTS[@]}" "${EDGE[@]}" -t "sentinel/$NODE_ID/telemetry" -m '{"probe":true}' >/dev/null 2>&1
sleep 2; r1=$(received)
[[ -n $r0 && ${r1:-0} -gt $r0 ]] && ok "ESP : publication sur sa télémétrie transmise à l'API" || ko "télémétrie de l'ESP non reçue ($r0 → $r1)"

r0=$(received)
mq mosquitto_pub "${MQ_OPTS[@]}" "${EDGE[@]}" -t "sentinel/intrus-99/telemetry" -m '{"probe":true}' >/dev/null 2>&1
mq mosquitto_pub "${MQ_OPTS[@]}" "${EDGE[@]}" -t "sentinel/autre-boitier/status" -m '{"online":true}' >/dev/null 2>&1
sleep 2; r1=$(received)
[[ -n $r0 && $r1 == "$r0" ]] && ok "ESP : usurpation d'un autre node_id bloquée par le broker" || ko "usurpation de node_id transmise ($r0 → $r1)"

tmp=$(mktemp)
mq mosquitto_sub "${MQ_OPTS[@]}" "${EDGE[@]}" -t "sentinel/$NODE_ID/cmd" -C 1 -W 10 >"$tmp" 2>&1 &
sub=$!; sleep 3
code=$(curl -sk -b "$JAR" -o /dev/null -w '%{http_code}' -H "Origin: https://$HOST" -H 'X-Requested-With: sentinel-dashboard' \
  -H 'Content-Type: application/json' -d "{\"node_id\":\"$NODE_ID\",\"action\":\"BEEP_2\"}" "https://$HOST/api/v1/actuators/control")
wait $sub
[[ $code == 202 ]] && grep -q BEEP_2 "$tmp" && ok "commande dashboard → 202 → reçue par l'ESP sur son topic cmd" || ko "commande non reçue (HTTP $code, $(cat "$tmp"))"

out=$(mq mosquitto_sub "${MQ_OPTS[@]}" "${EDGE[@]}" -t '$SYS/#' -C 1 -W 4 2>&1)
grep -q "Timed out" <<<"$out" && ok "ESP : messages \$SYS (retenus) jamais livrés" || ko "ESP lit \$SYS ($out)"
# statut retenu réellement présent sur le broker (même contenu que le Last Will)
mq mosquitto_pub "${MQ_OPTS[@]}" "${EDGE[@]}" -r -t "sentinel/$NODE_ID/status" -m '{"online":false}' >/dev/null 2>&1
out=$(mq mosquitto_sub "${MQ_OPTS[@]}" "${EDGE[@]}" -t 'sentinel/+/status' -C 1 -W 4 2>&1)
grep -q "Timed out" <<<"$out" && ok "ESP : statuts retenus des boîtiers jamais livrés (écriture seule)" || ko "ESP lit les statuts ($out)"
rm -f "$tmp" "$JAR"

check "port MQTT en clair 1883 absent" "! port_open $HOST 1883"
check "MQTTS 8883 : TLS 1.1 refusé" "! echo | openssl s_client -connect $HOST:8883 -tls1_1"

echo "== 6. Brute force"
codes=$(for i in $(seq 1 12); do api -X POST -H 'Content-Type: application/json' -d '{"username":"bruteforce","password":"x"}' "https://$HOST/api/v1/auth/login"; echo; done)
grep -q 429 <<<"$codes" && ok "limitation de débit sur /login (429)" || ko "pas de limitation sur /login"

echo "== 7. Secrets"
check ".env non suivi par Git" "! git ls-files --error-unmatch .env"
check "secrets/ non suivi par Git" "[ -z \"\$(git ls-files secrets infra-cyber/certs edge-firmware/include/secrets.h)\" ]"
check "aucun secret dans les variables d'environnement de l'API" "! docker compose exec -T api env | grep -qiE 'password=|secret=|api_key='"

echo ""
echo "Résultat : $PASS OK, $FAIL échec(s)"
[[ $FAIL -eq 0 ]]
