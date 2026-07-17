# 01 — Hardware Κόμβων Αισθητήρων (ESP32-C3 / ESP32 WROOM-32)

## 1. Ποιο chip χρησιμοποιείται πού

Το project χρησιμοποιεί δύο variants του ESP32 SoC family, όλα από την
Espressif, όλα με ενσωματωμένο ραδιόφωνο 2.4GHz — δεν υπάρχει εξωτερικό chip
ραδιοεπικοινωνίας πουθενά στο σύστημα:

| Ρόλος | Chip | Πηγή |
|---|---|---|
| Γέφυρα (bridge, rank 0) | ESP32-C3 | `firmware/bridge_esp32/bridge_esp32.ino:153` ("wait for USB CDC to connect on C3") |
| Κόμβος Ζώνης 1 | ESP32-C3 | `firmware/libraries/GreenhouseMesh/mesh_config.h:65` σχόλιο |
| Κόμβος Ζώνης 2 | ESP32 WROOM-32 | `firmware/libraries/GreenhouseMesh/mesh_config.h:66` σχόλιο, `firmware/edge_node_esp32/edge_node_esp32.ino` |
| Κάμερα | ESP32 (AI-Thinker ESP32-CAM module) | `firmware/cam_esp32/cam_esp32.ino` — pin map ταιριάζει με το γνωστό AI-Thinker layout |

Το ESP32-C3 είναι single-core RISC-V @ 160MHz, ενώ το κλασικό ESP32
WROOM-32 είναι dual-core Xtensa LX6 @ 240MHz. Και τα δύο μοιράζονται το ίδιο
radio front-end IP (Wi-Fi 802.11 b/g/n + Bluetooth LE), γι' αυτό ο ίδιος
κώδικας mesh (`mesh_node.h`) τρέχει αναλλοίωτος και στα δύο — το ESP-NOW API
είναι πανομοιότυπο.

## 2. Ραδιόφωνο / Κεραία

- **Ζώνη συχνοτήτων:** αποκλειστικά 2.4GHz ISM band (802.11 b/g/n PHY). Το
  ESP32/ESP32-C3 **δεν** έχει radio 5GHz — άρα ούτε το ESP-NOW ούτε το WiFi
  STA mode μπορούν ποτέ να χρησιμοποιήσουν το 5GHz δίκτυο ενός dual-band
  router, ακόμα κι αν το ίδιο SSID το εκπέμπει.
- **Κεραία:** τα boards που χρησιμοποιούνται ("Super Mini" ESP32-C3 modules
  βάσει `docs/EDGE_NODE_POWER_OPTIMIZATION.md`) έχουν ενσωματωμένη κεραία
  τυπωμένη στο PCB (PCB trace antenna), όχι εξωτερικό κονέκτορα κεραίας.
  Αυτό είναι μία ρύθμιση hardware, όχι κάτι που ρυθμίζει ο κώδικας.
- **Ισχύς εκπομπής (TX power):** ο κώδικας δεν καλεί ποτέ
  `esp_wifi_set_max_tx_power()`, άρα μένει στο εργοστασιακό default του SDK
  (τυπικά ~20dBm / 100mW max για 802.11b σε ESP32). Καμία ρητή ρύθμιση ισχύος
  δεν υπάρχει στο firmware.
- **Κανάλι:** το ESP-NOW δεν έχει δικό του κανάλι· χρησιμοποιεί όποιο κανάλι
  είναι ήδη συντονισμένο το radio του chip. Ο κάθε edge node **δεν συνδέεται
  ποτέ** στο WiFi (`WiFi.disconnect()` αμέσως μετά το `WiFi.mode(WIFI_STA)`,
  `edge_node_esp32_c3.ino:83`) — μπαίνει σε promiscuous mode μόνο για να
  σαρώσει (`WiFi.scanNetworks()`) και να βρει σε ποιο κανάλι εκπέμπει το
  σπιτικό router (`getWiFiChannel()`, `edge_node_esp32_c3.ino:39-45`), μετά
  συντονίζει το radio σε αυτό το κανάλι χειροκίνητα
  (`esp_wifi_set_channel()`, γραμμή 89) χωρίς ποτέ να κάνει association. Αυτό
  εξασφαλίζει ότι όλοι οι κόμβοι + η γέφυρα μιλάνε στο ίδιο κανάλι (η γέφυρα
  παίρνει το κανάλι της αυτόματα όταν κάνει πραγματικό `WiFi.begin()` στο
  router). Αν ο router αλλάξει κανάλι ενώ ένας κόμβος είναι unrouted για πάνω
  από `MESH_RESCAN_AFTER_MS` (60s), ξανασαρώνει (γραμμές 145-157).

## 3. ADC — Ανάγνωση υγρασίας εδάφους

- Pin: `SOIL_DATA_PIN = 2` → αντιστοιχεί σε `ADC1_CH2` στο ESP32-C3.
- Ανάλυση: το Arduino-ESP32 core διαβάζει το ADC1 του ESP32-C3 σε **12-bit**
  ανάλυση (εύρος τιμών 0–4095) στο default του `analogRead()`. Το επιβεβαιώνει
  και η βαθμονόμηση στον κώδικα: `SOIL_DRY_VAL = 3163`, `SOIL_WET_VAL = 1529`
  (`edge_node_esp32_c3.ino:18-19`) — και οι δύο τιμές μέσα στο 0–4095 εύρος.
- Ο αισθητήρας είναι χωρητικός (capacitive) αισθητήρας υγρασίας εδάφους —
  παράγει τάση ανάλογη με τη διηλεκτρική σταθερά του χώματος γύρω του, όχι
  αντιστατικός (οι αντιστατικοί διαβρώνονται γρήγορα σε συνεχή χρήση σε χώμα).
- Μετατροπή raw→ποσοστό: γραμμική παρεμβολή αντεστραμμένη (μεγαλύτερη raw
  τιμή = πιο ξηρό χώμα σε αυτόν τον αισθητήρα) με clamp στο [0,100]:
  ```
  pct = 100 × (DRY_VAL − raw) / (DRY_VAL − WET_VAL)
  ```
  (`soilPercent()`, `edge_node_esp32_c3.ino:47-52`).

## 4. DHT22 — Θερμοκρασία/Υγρασία αέρα

- Pin: `DHT_DATA_PIN = 6` (GPIO6 — επιλέχθηκε σκόπιμα *όχι* πάνω σε JTAG pins
  του ESP32-C3, βλ. σχόλιο γραμμή 11).
- Πρωτόκολλο: το DHT22 (AM2302) χρησιμοποιεί ένα **proprietary single-wire
  bit-banged πρωτόκολλο** (όχι πραγματικό 1-Wire της Dallas/Maxim, παρόλο
  που μοιάζει) — timing-critical παλμοί (~80μs LOW/HIGH start, μετά 40-bit
  frame με μεταβλητό πλάτος παλμού ανά bit: ~26-28μs = '0', ~70μs = '1').
  Το χειρίζεται η βιβλιοθήκη `DHT.h` (Adafruit-style), όχι δικός μας κώδικας.
  Η ακρίβεια είναι ±0.5°C / ±2% RH, ανάλυση 0.1°C / 0.1% RH.
- Απαιτεί pull-up αντίσταση στη γραμμή δεδομένων — αν λείπει, το `readTemperature()`
  /`readHumidity()` επιστρέφουν `NaN` (ελέγχεται ρητά, γραμμή 132-134).

## 5. GPIO Power-Switching (εξοικονόμηση ενέργειας)

Και οι δύο αισθητήρες τροφοδοτούνται **όχι** απευθείας από 3.3V, αλλά από
δύο GPIO pins που λειτουργούν σαν διακόπτες:

```
SOIL_PWR_PIN = 4    DHT_PWR_PIN = 5
```

Ροή ανά κύκλο μέτρησης (state machine `PHASE_IDLE` → `PHASE_WARMUP`,
γραμμές 33-141):
1. `PHASE_IDLE`: κάθε `SEND_INTERVAL_MS` (5000ms) ανεβάζει και τα δύο GPIO
   σε `HIGH` (ενεργοποιεί τροφοδοσία αισθητήρων), μεταβαίνει σε `PHASE_WARMUP`.
2. `PHASE_WARMUP`: περιμένει `SENSOR_WARMUP_MS` (2000ms) — χρόνος
   σταθεροποίησης που απαιτεί το DHT22 μετά την τροφοδότηση.
3. Διαβάζει και τους δύο αισθητήρες, **μετά** κατεβάζει τα GPIO σε `LOW`.

Ένα GPIO του ESP32 μπορεί να δώσει ρεύμα (source) έως ~20mA — αρκετό για
DHT22 (~1.5mA) και τον χωρητικό αισθητήρα εδάφους (~5mA), όχι αρκετό για
βαρύτερα φορτία. Αυτή η τεχνική **κόβει εντελώς** το ρεύμα ηρεμίας (idle
draw) των αισθητήρων ανάμεσα σε μετρήσεις, χωρίς να χρειάζεται εξωτερικό
transistor/MOSFET.

## 6. Non-blocking scheduling — γιατί όχι `delay()`

Ο παλιός κώδικας (πριν το mesh) έκανε απλά `delay(2000); readSensors();
delay(5000);` σε loop. Με το mesh αυτό **δεν είναι πια αποδεκτό**: κάθε
κόμβος πρέπει ταυτόχρονα να:
- στέλνει το δικό του beacon στο σωστό trickle interval (`meshBeaconTick()`),
- ελέγχει αν ο γονέας του σιώπησε πολύ (`meshCheckParentTimeout()`),
- προωθεί (relay) πακέτα παιδιών του αν φτάσουν ασύγχρονα μέσω interrupt-driven
  ESP-NOW callback.

Ένα blocking `delay()` 2+ δευτερολέπτων θα "πάγωνε" όλα τα παραπάνω. Γι'
αυτό ο αισθητήρας-κύκλος έγινε ρητό state machine οδηγούμενο από
`millis()` (χωρίς blocking waits), και το κύριο `loop()` καλεί
`meshBeaconTick()` + `meshCheckParentTimeout()` σε **κάθε** iteration πριν
προχωρήσει στη λογική αισθητήρα (`edge_node_esp32_c3.ino:104-160`). Το
μοναδικό `delay(10)` στο τέλος του loop είναι καθαρά yield, όχι μέρος
κάποιας λογικής χρονισμού.

## 7. Μελλοντικό deep sleep (δεν έχει υλοποιηθεί ακόμα)

Το τρέχον firmware **δεν** κοιμάται ποτέ — το radio μένει πάντα ενεργό,
συνεχές ρεύμα ~86.5mA κατά την ενεργή φάση. Το πλήρες σχέδιο
duty-cycling (deep sleep, GPIO-cut LED, LDO bypass, LiFePO4 18650 + ηλιακό)
είναι τεκμηριωμένο στο `docs/EDGE_NODE_POWER_OPTIMIZATION.md`, αλλά είναι
**σχέδιο, όχι κώδικας** — καμία γραμμή deep-sleep δεν υπάρχει ακόμα στο
`.ino`. Το wire-format του mesh (πεδίο `window_duration_ms` στο `MeshBeacon`)
είναι ήδη forward-compatible με αυτό (βλ. `03-mesh-routing.md`), ώστε να μη
χρειαστεί αλλαγή πρωτοκόλλου όταν προστεθεί.
