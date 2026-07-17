# Τεχνική Τεκμηρίωση GreenHouse — Ευρετήριο

**Σκοπός:** Πλήρης, σχεδόν σε επίπεδο OSI, τεχνική ανάλυση κάθε υποσυστήματος
του project — τι πρωτόκολλα/θύρες/πακέτα χρησιμοποιούνται, πώς δουλεύει ο
κώδικας βήμα-βήμα, και **γιατί** επιλέχθηκε κάθε τεχνολογία έναντι των
εναλλακτικών (π.χ. γιατί SQLite και όχι MariaDB). Γραμμένο για τη διπλωματική
— λεπτομέρεια > συντομία.

Αυτά τα έγγραφα **συμπληρώνουν**, δεν αντικαθιστούν:
- `docs/ARCHITECTURE.md` — το συνολικό διάγραμμα ροής (Mermaid), καλό σημείο
  εκκίνησης πριν μπεις στη λεπτομέρεια εδώ.
- `docs/MESH_RELAY_EXPLAINED.md` — η απλή-γλώσσα εξήγηση του mesh relay
  (καλή για παρουσίαση). Το `03-mesh-routing.md` εδώ είναι η τεχνική εκδοχή
  με ακριβή αλγόριθμο και δομές πακέτων.
- `docs/EDGE_NODE_POWER_OPTIMIZATION.md` — μελλοντικό deep-sleep σχέδιο
  (δεν έχει υλοποιηθεί ακόμα).

## Δομή

Η ροή δεδομένων ακολουθεί τη φυσική διαδρομή ενός μετρήσεως: από το αισθητήρα
μέχρι την οθόνη του κινητού. Τα έγγραφα είναι σε αυτή τη σειρά:

| # | Αρχείο | Τι καλύπτει |
|---|---|---|
| 01 | [`01-sensor-node-hardware.md`](01-sensor-node-hardware.md) | ESP32-C3 hardware: κεραία/ραδιόφωνο, ADC, αισθητήρες, GPIO power-switching |
| 02 | [`02-esp-now-protocol.md`](02-esp-now-protocol.md) | ESP-NOW σε OSI layers: PHY/MAC, κρυπτογράφηση, γιατί όχι BLE mesh/Zigbee/LoRa |
| 03 | [`03-mesh-routing.md`](03-mesh-routing.md) | Ο αλγόριθμος rank/beacon/trickle σε επίπεδο δομών & pseudocode |
| 04 | [`04-bridge-gateway.md`](04-bridge-gateway.md) | Η γέφυρα ESP32: WiFi STA, MQTT client, offline detection |
| 05 | [`05-mqtt-broker.md`](05-mqtt-broker.md) | Mosquitto: θύρες, TLS, πλήρες δέντρο topics, QoS/retain |
| 06 | [`06-database.md`](06-database.md) | Σχήμα SQLite + γιατί SQLite και όχι MariaDB/PostgreSQL/InfluxDB |
| 07 | [`07-recorder-service.md`](07-recorder-service.md) | Buffering στη RAM, batched writes, hourly rollup, retention |
| 08 | [`08-cloud-bridge.md`](08-cloud-bridge.md) | HiveMQ Cloud bridge — γιατί custom paho-mqtt και όχι το native bridge του Mosquitto |
| 09 | [`09-setup-portal.md`](09-setup-portal.md) | Captive portal, first-boot provisioning, ζεύξη (pairing) |
| 10 | [`10-security.md`](10-security.md) | Πλήρης χάρτης κρυπτογράφησης/αυθεντικοποίησης σε κάθε επίπεδο |
| 11 | [`11-weather-automation.md`](11-weather-automation.md) | Open-Meteo integration, μηχανή κανόνων αυτοματισμού |
| 12 | [`12-camera-motion.md`](12-camera-motion.md) | ESP32-CAM, ανίχνευση κίνησης, chunked MQTT για εικόνες |
| 13 | [`13-mobile-app.md`](13-mobile-app.md) | Αρχιτεκτονική Flutter app (Riverpod, connection/repository layers) |
| 14 | [`14-network-reference.md`](14-network-reference.md) | Συγκεντρωτικός πίνακας: κάθε θύρα/πρωτόκολλο/OSI layer σε όλο το σύστημα |

## Σύμβαση αναφορών κώδικα

Κάθε ισχυρισμός παραπέμπει σε πραγματικό αρχείο/γραμμή του repo (μορφή
`path/to/file.ext:NN`) ώστε να επαληθεύεται απευθείας στον πηγαίο κώδικα.
Όπου περιγράφεται κάτι που **δεν** υπάρχει ακόμα υλοποιημένο (π.χ. deep sleep,
πραγματικός actuator controller), σημειώνεται ρητά ως τέτοιο — δεν
εφευρίσκουμε λεπτομέρειες.
