# 04 — Γέφυρα (Bridge ESP32)

Ο κόμβος-γέφυρα (`firmware/bridge_esp32/bridge_esp32.ino`) είναι ο μοναδικός
κόμβος που μιλάει **δύο** πρωτόκολλα ταυτόχρονα: ESP-NOW προς τους
αισθητήρες, και ένα σύρμα UART προς το Raspberry Pi. Τροφοδοτείται από
ρεύμα (όχι μπαταρία), γι' αυτό δεν κάνει καμία εξοικονόμηση ενέργειας.

> **Update:** η γέφυρα δεν χρησιμοποιεί πλέον WiFi/MQTT/TLS καθόλου — δες
> §2 παρακάτω. Πηγή αλήθειας: `docs/superpowers/specs/2026-07-20-uart-bridge-design.md`.
> Η παλιά WiFi/MQTT υλοποίηση παραμένει μόνο στο git history (πριν την
> commit `25e8f0f`), όχι στο τρέχον firmware.

## 1. Ρόλος στο mesh — rank 0 άγκυρα

Όπως αναλύεται στο `03-mesh-routing.md`, η γέφυρα δεν επιλέγει ποτέ γονέα —
είναι πάντα `rank = 0`, το σταθερό σημείο αναφοράς όλου του δικτύου. Στέλνει
το δικό της beacon σε **σταθερό** interval 2000ms χωρίς trickle backoff
(`MESH_BRIDGE_BEACON_INTERVAL_MS`) — δεν υπάρχει λόγος οικονομίας αφού
τροφοδοτείται μόνιμα. Το ίδιο interval τροφοδοτεί και το heartbeat της §2.

## 2. UART link προς το Pi — αντικατέστησε εντελώς το WiFi/MQTT/TLS

Η γέφυρα γράφει μία γραμμή JSON ανά γεγονός στο `Serial1` (**όχι** `Serial2`
— το ESP32-C3 έχει μόνο δύο hardware UART controllers, `Serial`
[USB-CDC, χρησιμοποιείται μόνο για debug logs] και `Serial1`):

```c
Serial1.begin(115200, SERIAL_8N1, /*RX=*/5, /*TX=*/4);
```

**Καλωδίωση** (και οι δύο πλευρές 3.3V λογική — καμία μετατροπή τάσης):

```
ESP32-C3 GPIO4 (TX) ──────────► Pi physical pin 10 = GPIO15 (RXD)
ESP32-C3 GPIO5 (RX) ◄────────── Pi physical pin  8 = GPIO14 (TXD)
ESP32-C3 GND         ─────────── Pi GND (οποιοδήποτε GND pin)
```

Αποφυγή GPIO2/8/9 (C3 SuperMini boot-strapping pins / onboard LED). Στο Pi,
το `/dev/serial0` πρέπει πρώτα να ελευθερωθεί από το login console
(`sudo raspi-config` → Interface Options → Serial Port → όχι login shell,
ναι hardware enabled → reboot) — εφάπαξ χειροκίνητο βήμα OS, τεκμηριωμένο
στο `INSTRUCTIONS.md`, όχι κάτι που κάνει αυτόματα το `install.sh` (ρίσκο
να "κλειδώσει" κάποιον έξω από serial console σε μονάδα που το χρειάζεται).

**Γιατί:** αφαιρεί την εξάρτηση από σπιτικό router, ολόκληρο τον TLS/MQTT
client stack από το ESP32, και τα credentials WiFi/MQTT που ήταν πριν
hardcoded στο firmware (`IMPROVEMENTS.md §Α1`). Κατάλληλο μόνο όταν γέφυρα
και Pi είναι φυσικά κοντά (καλωδιακή απόσταση) — δες το design spec's
Non-goals για τα όρια αυτής της απόφασης.

## 3. Πρωτόκολλο — newline-delimited JSON, ένα object ανά γραμμή

Καμία binary encoding· ίδια φιλοσοφία με κάθε άλλο payload σε αυτό το
project (πάντα human-readable strings/JSON, ποτέ packed binary). Έξι τύποι
γραμμών:

```json
{"type":"heartbeat","mac":"206EF16C6B50"}
{"type":"reading","zone":"zone1","group":"air","metric":"temperature","value":23.4}
{"type":"status","mac":"206EF16CA1B0","status":"online"}
{"type":"battery","mac":"206EF16CA1B0","pct":76.0}
{"type":"mesh","mac":"206EF16CA1B0","parent":"206EF16C6B50","rank":1,"rssi":-55,"sleepy":true,"battery_mv":3312,"zone":"zone1"}
```

Το Pi-side `pi/scripts/serial_bridge.py` διαβάζει αυτές τις γραμμές από
`/dev/serial0` και τις ξαναδημοσιεύει στον τοπικό loopback Mosquitto —
**ίδια topics/payloads/retain συμπεριφορά** με πριν (§6), ώστε recorder/
weather/portal/HiveMQ-bridge/app να μη βλέπουν καμία διαφορά.

## 4. Heartbeat — αντικατέστησε το παλιό MQTT Last-Will

Πριν, η γέφυρα ήταν η ίδια ο MQTT client· ένας θάνατος της σήμαινε
αποσυνδεδεμένο TCP socket, και ο broker ενεργοποιούσε αυτόματα το LWT
(`greenhouse/nodes/<own-mac>/status` = `offline`). Τώρα ο **μοναδικός** MQTT
client είναι το `serial_bridge.py` στο Pi — ένα UART link δεν έχει
"σύνδεση" με την έννοια του TCP (η γέφυρα απλά συνεχίζει να κάνει
`Serial1.println()` ό,τι κι αν συμβαίνει στην άλλη άκρη).

Λύση: η γέφυρα στέλνει `{"type":"heartbeat","mac":"<own>"}` κάθε 2000ms
(ίδιο interval με το beacon, §1). Το `serial_bridge.py` κρατά πότε άκουσε
τελευταία heartbeat και δημοσιεύει `offline` (retained) αν περάσουν
`MESH_OFFLINE_AFTER × 2000ms` = 6s χωρίς κανένα· `online` (retained) στο
πρώτο heartbeat μετά από τέτοια σιωπή (ή στο πρώτο ποτέ). Ίδιο μοτίβο
πολλαπλασιαστή με το offline detection των edge nodes (`03-mesh-routing.md §9`).

## 5. Zone lookup βάσει `origin_mac`, όχι άμεσου αποστολέα

```c
int idx = meshTrustedIndex(pkt.origin_mac);   // ΟΧΙ info->src_addr
```

Αυτή είναι η **μοναδική** λειτουργική αλλαγή που έφερε το multi-hop mesh
στη γέφυρα σε σχέση με το παλιό star-topology σχέδιο: πριν, ο ESP-NOW
`src_addr` ήταν πάντα ο πραγματικός αισθητήρας (κάθε κόμβος έστελνε
απευθείας). Τώρα, το `src_addr` μπορεί να είναι ένας ενδιάμεσος relay — το
`origin_mac` μέσα στο ίδιο το `MeshDataPacket` είναι το μόνο αξιόπιστο
στοιχείο για ποιος πραγματικά μέτρησε. Άσχετο με το UART/WiFi transport
της §2 — καθαρά mesh-επίπεδο λογική, αμετάβλητη.

## 6. Δημοσίευση MQTT (μέσω `serial_bridge.py`) — topics, retain

Για κάθε έγκυρο πακέτο (ranked, de-dup-checked, γνωστό `origin_mac`):

```
greenhouse/<zone>/air/temperature     → "%.1f"
greenhouse/<zone>/air/humidity        → "%.1f"
greenhouse/<zone>/soil/moisture       → "%.1f"
greenhouse/nodes/<MAC-hex>/status     → "online"
greenhouse/nodes/<MAC-hex>/battery    → "%.1f"  (μόνο αν battery_mv > 0)
greenhouse/nodes/<MAC-hex>/mesh       → JSON, δες 03-mesh-routing.md
```

Όλα με **`retain = true`**. Το mv→% mapping (LiFePO4 discharge curve,
piecewise-linear) γίνεται στη γέφυρα (`batteryPctFromMv()`), όχι στο Pi —
ένα σημείο συντήρησης για την καμπύλη.

## 7. Ανίχνευση offline κόμβων

Ανάλυση αλγορίθμου στο `03-mesh-routing.md §9`. Η γέφυρα η ίδια δεν
"γνωρίζει" πλέον αν το MQTT της Pi-πλευράς είναι συνδεδεμένο (δεν είναι
πια MQTT client) — στέλνει πάντα τη γραμμή UART, ό,τι κι αν συμβαίνει στην
άλλη άκρη (δες §4). Ο έλεγχος `checkOfflineNodes()` τρέχει κάθε 1000ms
ανεξάρτητα, per-role expected interval (sleepy vs always-on, δες
`docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md §Bridge-side liveness`).
