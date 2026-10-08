# Station capteurs ESP32

ESP32 avec écran OLED, capteur température/humidité, détecteur de mouvement et capteur de gaz, connecté en Wi-Fi.

## Matériel et câblage

| Composant | Broche composant | ESP32 | Remarque |
|---|---|---|---|
| OLED SSD1306 128×32 (I2C, 0x3C) | SDA / SCL | GPIO21 / GPIO22 | Bus I2C par défaut |
| DHT22 (boîtier blanc) | DATA | GPIO18 | Module 3 broches : pull-up intégrée |
| PIR | OUT | GPIO23 | |
| LED | + (via résistance) | GPIO19 | S'allume si mouvement, clignote en alarme |
| Buzzer | + | GPIO5 | Alarme et bips commandés à distance |
| MQ « Flying Fish » | VCC | VIN / 5V | Le chauffage exige 5 V (~150 mA) |
| | AO | GPIO34 **via pont diviseur** | 10 kΩ (AO→GPIO) + 20 kΩ (GPIO→GND) |
| | DO | non branché | Sort 5 V : diviseur requis si utilisé |

Toutes les masses (GND) sont communes.

## Mise en route

1. Installer l'extension **PlatformIO IDE** dans VS Code.
2. `include/secrets.h` (jamais versionné) est généré par `bash scripts/setup.sh` à la racine du dépôt :
   CA interne, `NODE_ID`, mot de passe MQTT et IP du broker y sont déjà. Renseigner seulement
   `WIFI_SSID` / `WIFI_PASSWORD` (modèle : `include/secrets.h.example`).
3. Compiler : ✓ ou `pio run`
4. Téléverser et ouvrir le moniteur : `pio run -t upload -t monitor` — attendu : `[MQTT] Connexion TLS... OK`

Les bibliothèques déclarées dans `platformio.ini` (`lib_deps`) se téléchargent automatiquement.

## Sécurité de la liaison MQTT

- **MQTTS (TLS 1.2+)** sur le port 8883 ; le certificat du broker est vérifié avec la CA interne (`setCACert`).
  Un faux broker (attaque de l'homme du milieu) est refusé : `tls=...` s'affiche dans le moniteur.
- **Authentification** : le compte MQTT est le `NODE_ID` ; le broker force l'identifiant client à ce nom.
- **ACL du broker (refus par défaut)** — le boîtier n'a accès qu'à 3 topics :

  | Topic | Droit | Rôle |
  |---|---|---|
  | `sentinel/<NODE_ID>/telemetry` | écriture | mesures toutes les 2 s |
  | `sentinel/<NODE_ID>/status` | écriture | `online` (retenu), Last Will `{"online":false}`, accusé des commandes |
  | `sentinel/<NODE_ID>/cmd` | lecture | ordres du serveur |

  Tout le reste est refusé : un boîtier volé ne peut ni lire les autres boîtiers, ni se faire passer
  pour un autre `node_id`, ni s'envoyer des commandes, ni lire `$SYS`.
- **Commandes validées** : topic exact, 128 octets max, JSON, liste blanche
  (`ALARM_ON`, `ALARM_OFF`, `BEEP_2`, `BEEP_3`, `LED_ON`, `LED_OFF`), exécution hors callback.
- **Pas d'horloge** : le serveur horodate les mesures ; l'ESP envoie un compteur `seq` (anti-rejeu côté serveur).

## Affichage OLED (4 lignes)

1. État : « MQTTS OK », « ALARME ACTIVE », « MQTTS deconnecte » ou « WiFi deconnecte »
2. État du PIR
3. Température / humidité
4. Gaz : valeur brute (0 à 4095) et tension réelle

## Points d'attention

- **Le capteur est un DHT22.** Avec `DHTTYPE DHT11`, les valeurs lues sont absurdes (par exemple 1,9 °C et 17 %).
- **Le capteur MQ doit être lu sur l'ADC1 (GPIO32 à 39).** L'ADC2 ne fonctionne pas quand le Wi-Fi est actif.
- **Ne jamais envoyer plus de 3,3 V sur une broche de l'ESP32.** C'est la raison du pont diviseur sur AO ; la tension réelle vaut la tension mesurée × 1,5.
- **Préchauffage du MQ** : 24 à 48 h à la première utilisation, puis 1 à 3 min à chaque démarrage. Ses valeurs sont relatives, il ne mesure pas directement des ppm.
- **La police de l'OLED ne gère pas les accents** : écrire les textes à afficher sans accents.
- **DHT22 : au moins 2 s entre deux lectures** (le délai de `loop()`).

## Dépannage

| Symptôme | Cause probable / solution |
|---|---|
| Upload bloqué sur « Connecting... » | Maintenir BOOT au début de l'upload, puis appuyer sur EN |
| Aucun port détecté | Câble USB qui ne transporte que le courant, pilote CP2102/CH340 manquant, ou (sous Linux) règles udev et groupe `dialout` |
| L'upload échoue en cours de route | Ajouter `upload_speed = 460800` (ou 115200) dans `platformio.ini` |
| Caractères illisibles au démarrage | Normal : le bootloader parle à 74880 bauds |
| « Erreur capteur DHT » | Vérifier que DATA arrive bien sur GPIO18 et que `DHTTYPE` est DHT22 |
