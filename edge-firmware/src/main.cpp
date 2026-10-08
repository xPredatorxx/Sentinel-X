// SENTINEL-X — firmware du boîtier Edge (ESP32 DevKit v1)
//
// Sécurité :
// - MQTTS (TLS 1.2) avec vérification du certificat du broker par la CA interne
//   (setCACert) : pas de connexion possible vers un faux broker ;
// - authentification par identifiant / mot de passe (compte MQTT = NODE_ID) ;
// - 3 topics seulement, imposés par l'ACL du broker :
//     sentinel/<NODE_ID>/telemetry  (publication)
//     sentinel/<NODE_ID>/status     (publication retenue + Last Will)
//     sentinel/<NODE_ID>/cmd        (abonnement)
// - commandes validées strictement : topic exact, taille bornée, JSON, liste blanche ;
// - aucun horodatage envoyé : le serveur date les mesures à la réception.
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "DHT.h"
#include "secrets.h"

String topicTelemetry;  // sentinel/<id>/telemetry
String topicStatus;     // sentinel/<id>/status
String topicCmd;        // sentinel/<id>/cmd

WiFiClientSecure net;
PubSubClient mqtt(net);
unsigned long lastPublish = 0;
unsigned long lastSensor = 0;
unsigned long lastWifiRetry = 0;
unsigned long lastMqttRetry = 0;
uint32_t seq = 0;

const unsigned long PUBLISH_INTERVAL_MS = 2000;
const unsigned long SENSOR_INTERVAL_MS = 1000;
const unsigned long RECONNECT_INTERVAL_MS = 5000;
const unsigned long WIFI_RESET_AFTER_MS = 30000;
const unsigned int MAX_CMD_LEN = 128;

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 32

#define DHTPIN 18
#define DHTTYPE DHT22
DHT dht(DHTPIN, DHTTYPE);

// Écran SSD1306 en I2C (SDA=21, SCL=22 par défaut sur ESP32)
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

const int pirPin = 23;  // capteur PIR
const int ledPin = 19;  // LED
const int mqPin = 34;   // capteur de gaz MQ-2 (ADC1, via pont diviseur)
const int BUZZER_PIN = 5;
bool motion = false;
int gasRaw = 0;
float gasVolts = NAN;

// --- Commandes ------------------------------------------------------------------
enum Action { NONE, ALARM_ON, ALARM_OFF, BEEP_2, BEEP_3, LED_ON, LED_OFF };
struct Command { Action action; uint32_t id; const char* name; };
volatile bool cmdPending = false;
Command pendingCmd = {NONE, 0, ""};

bool alarmActive = false;
bool ledForced = false;
unsigned long lastAlarmToggle = 0;
bool alarmPhase = false;

Action parseAction(const char* s, const char** name) {
  static const struct { const char* name; Action action; } table[] = {
    {"ALARM_ON", ALARM_ON}, {"ALARM_OFF", ALARM_OFF}, {"BEEP_2", BEEP_2},
    {"BEEP_3", BEEP_3},     {"LED_ON", LED_ON},       {"LED_OFF", LED_OFF},
  };
  for (auto& e : table) {
    if (strcmp(s, e.name) == 0) { *name = e.name; return e.action; }
  }
  return NONE;
}

void bip(int frequence, int dureeMs) {
  tone(BUZZER_PIN, frequence, dureeMs);
  delay(dureeMs);
  noTone(BUZZER_PIN);
}

// 2 bips aigus : présence détectée (PIR ou caméra)
void bip2() {
  bip(1500, 100);
  delay(50);
  bip(1500, 100);
}

// mélodie Do-Mi-Sol : démarrage et anomalie détectée par l'IA prédictive
void bip3() {
  bip(1047, 120);   // Do aigu
  delay(60);
  bip(1319, 120);   // Mi
  delay(60);
  bip(1568, 200);   // Sol
}

// Le callback ne fait que valider et mettre en file : l'action est exécutée dans loop().
void onMessage(char* topic, byte* payload, unsigned int len) {
  if (topicCmd != topic) return;                 // seul le topic de commande est accepté
  if (len == 0 || len > MAX_CMD_LEN) {
    Serial.println("[CMD] rejetée : taille invalide");
    return;
  }
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) {
    Serial.println("[CMD] rejetée : JSON invalide");
    return;
  }
  if (!doc["id"].is<uint32_t>() || !doc["action"].is<const char*>()) {
    Serial.println("[CMD] rejetée : champs manquants");
    return;
  }
  const char* name = "";
  Action a = parseAction(doc["action"].as<const char*>(), &name);
  if (a == NONE) {
    Serial.println("[CMD] rejetée : action inconnue");
    return;
  }
  pendingCmd = {a, doc["id"].as<uint32_t>(), name};
  cmdPending = true;
  Serial.printf("[CMD] #%lu %s acceptée\n", (unsigned long)pendingCmd.id, name);
}

void publishStatus(const Command* done, bool ok) {
  JsonDocument doc;
  doc["online"] = true;
  doc["alarm"] = alarmActive;
  if (done) {
    doc["cmd_id"] = done->id;
    doc["cmd"] = done->name;
    doc["ok"] = ok;
  }
  char buf[128];
  size_t n = serializeJson(doc, buf, sizeof buf);
  mqtt.publish(topicStatus.c_str(), (const uint8_t*)buf, n, true);   // retenu
}

void executeCommand(const Command& c) {
  switch (c.action) {
    case ALARM_ON:  alarmActive = true; break;
    case ALARM_OFF: alarmActive = false; noTone(BUZZER_PIN); break;
    case BEEP_2:    bip2(); break;
    case BEEP_3:    bip3(); break;
    case LED_ON:    ledForced = true; break;
    case LED_OFF:   ledForced = false; break;
    default: return;
  }
  publishStatus(&c, true);   // accusé de réception via le topic de statut
}

void connectMqtt() {
  Serial.print("[MQTT] Connexion TLS... ");
  // Last Will : si le boîtier disparaît, le broker publie {"online":false} à sa place
  if (mqtt.connect(NODE_ID, NODE_ID, MQTT_PASS, topicStatus.c_str(), 1, true, "{\"online\":false}")) {
    Serial.println("OK");
    publishStatus(nullptr, true);
    mqtt.subscribe(topicCmd.c_str(), 1);
  } else {
    char err[100] = "";
    net.lastError(err, sizeof err);   // distingue un problème de certificat d'un refus d'identifiants
    Serial.printf("échec (rc=%d, tls=%s)\n", mqtt.state(), err);
  }
}

void publishTelemetry(float t, float h) {
  JsonDocument doc;
  doc["seq"] = ++seq;
  doc["uptime_ms"] = millis();
  if (!isnan(t)) doc["temp"] = roundf(t * 10) / 10.0f; else doc["temp"] = nullptr;
  if (!isnan(h)) doc["hum"] = roundf(h * 10) / 10.0f; else doc["hum"] = nullptr;
  doc["gas_raw"] = gasRaw;
  if (!isnan(gasVolts)) doc["gas_v"] = roundf(gasVolts * 100) / 100.0f;
  doc["motion"] = motion;
  doc["rssi"] = WiFi.RSSI();
  char buf[192];
  size_t n = serializeJson(doc, buf, sizeof buf);
  mqtt.publish(topicTelemetry.c_str(), (const uint8_t*)buf, n, false);
}

void setup() {
  Serial.begin(115200);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {  // 0x3D pour certains 128x64
    Serial.println(F("SSD1306 allocation failed"));
    for (;;);
  }
  delay(2000);
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  pinMode(pirPin, INPUT);
  pinMode(ledPin, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  bip3();

  dht.begin();

  analogSetPinAttenuation(mqPin, ADC_11db);

  String base = String("sentinel/") + NODE_ID;
  topicTelemetry = base + "/telemetry";
  topicStatus    = base + "/status";
  topicCmd       = base + "/cmd";

  // Wi-Fi
  WiFi.mode(WIFI_STA);
#ifdef ESP_STATIC_IP
  // adresse fixe du plan d'adressage (192.168.137.20) : le serveur et le pare-feu savent qui est le boîtier
  IPAddress ip, gw, mask(255, 255, 255, 0);
  ip.fromString(ESP_STATIC_IP);
  gw.fromString(ESP_GATEWAY);
  WiFi.config(ip, gw, mask, gw);
#endif
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
      Serial.printf("[WiFi] deconnecte, raison=%d\n", info.wifi_sta_disconnected.reason);
    else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP)
      Serial.printf("[WiFi] IP obtenue : %s\n", WiFi.localIP().toString().c_str());
    else if (event == ARDUINO_EVENT_WIFI_STA_LOST_IP)
      Serial.println("[WiFi] IP perdue");
  });
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  WiFi.setSleep(false);          // la veille modem fait souvent éjecter l'ESP32 par le hotspot Windows
  WiFi.setAutoReconnect(true);

  Serial.print("Connexion a ");
  Serial.println(WIFI_SSID);

  display.setCursor(0, 0);
  display.println("Connexion en cours...");
  display.display();

  // TLS : le certificat présenté par le broker doit être signé par NOTRE CA
  net.setCACert(MQTT_CA_CERT);
  net.setHandshakeTimeout(10);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);   // 256 octets par défaut
  mqtt.setKeepAlive(30);     // le LWT se déclenche après ~1,5 × keepalive sans signe de vie
  mqtt.setSocketTimeout(5);  // 15 s par défaut : gèlerait loop() si le broker ne répond pas
  mqtt.setCallback(onMessage);

  unsigned long debut = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");

    if (millis() - debut > 20000) {  // abandon après 20 s, loop() réessaiera
      Serial.println("\nEchec de connexion, statut : " + String(WiFi.status()));

      display.clearDisplay();
      display.setCursor(0, 0);
      display.println("Echec connexion");  // police par défaut : pas d'accents
      display.print("Statut : ");
      display.println(WiFi.status());
      display.display();
      return;
    }
  }

  Serial.println("\nConnecte !");
  Serial.print("Adresse IP : ");
  Serial.println(WiFi.localIP());
}

const unsigned long DHT_INTERVAL_MS = 2000;
unsigned long lastDhtRead = 0;
float lastH = NAN;
float lastT = NAN;

void loop() {
  unsigned long now = millis();

  // état Wi-Fi
  bool wifiOk = WiFi.status() == WL_CONNECTED && WiFi.localIP() != IPAddress(0, 0, 0, 0);
  // La reconnexion automatique de l'ESP32 gère les coupures courtes. On ne relance la connexion
  // à la main qu'après 30 s sans réseau : un disconnect()/begin() toutes les 5 s interromprait
  // la reconnexion automatique en plein échange de clés et entretiendrait les déconnexions.
  if (wifiOk) {
    lastWifiRetry = now;
  } else if (now - lastWifiRetry >= WIFI_RESET_AFTER_MS) {
    lastWifiRetry = now;
    Serial.println("WiFi absent depuis 30 s, relance de la connexion...");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }

  // DHT22 doit être lu max toutes les 2 secondes
  if (now - lastDhtRead >= DHT_INTERVAL_MS) {
    lastDhtRead = now;
    float currentH = dht.readHumidity();
    float currentT = dht.readTemperature();
    if (!isnan(currentH) && !isnan(currentT)) {
      lastH = currentH;
      lastT = currentT;
    } else {
      Serial.println(F("Failed to read from DHT sensor!"));
    }
  }

  if (now - lastSensor >= SENSOR_INTERVAL_MS) {
    lastSensor = now;

    display.clearDisplay();
    display.setCursor(0, 0);
    if (!wifiOk) display.println("WiFi deconnecte");
    else if (!mqtt.connected()) display.println("MQTTS deconnecte");
    else display.println(alarmActive ? "ALARME ACTIVE" : "MQTTS OK");

    // détection de mouvement
    motion = digitalRead(pirPin) == HIGH;
    display.println(motion ? "Mouvement detecte" : "Surveillance...");

    display.printf("T: %.1fC  H: %.0f%%\n", lastT, lastH);

    // capteur de gaz : valeur brute ADC (0-4095) + tension réelle (pont diviseur x1,5)
    gasRaw = analogRead(mqPin);
    gasVolts = analogReadMilliVolts(mqPin) * 1.5 / 1000.0;
    display.printf("Gaz: %d (%.2fV)", gasRaw, gasVolts);

    display.display();
  }

  // LED : forcée par commande, sinon allumée sur mouvement ; clignote en alarme
  if (alarmActive) digitalWrite(ledPin, alarmPhase ? HIGH : LOW);
  else digitalWrite(ledPin, (ledForced || motion) ? HIGH : LOW);

  // sirène non bloquante
  if (alarmActive && now - lastAlarmToggle >= 300) {
    lastAlarmToggle = now;
    alarmPhase = !alarmPhase;
    tone(BUZZER_PIN, alarmPhase ? 2000 : 1500);
  }

  if (wifiOk && !mqtt.connected() && now - lastMqttRetry > RECONNECT_INTERVAL_MS) {
    lastMqttRetry = now;
    connectMqtt();
  }

  if (mqtt.connected()) {
    mqtt.loop();

    if (cmdPending) {
      cmdPending = false;
      executeCommand(pendingCmd);
    }

    if (now - lastPublish >= PUBLISH_INTERVAL_MS) {
      lastPublish = now;
      publishTelemetry(lastT, lastH);
    }
  }
}
