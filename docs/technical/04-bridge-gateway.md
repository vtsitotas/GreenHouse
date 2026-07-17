# 04 — Γέφυρα (Bridge ESP32)

Ο κόμβος-γέφυρα (`firmware/bridge_esp32/bridge_esp32.ino`) είναι ο μοναδικός
κόμβος που μιλάει **δύο** πρωτόκολλα ταυτόχρονα: ESP-NOW προς τους
αισθητήρες, και WiFi+MQTT+TLS προς το Raspberry Pi. Τροφοδοτείται από ρεύμα
(όχι μπαταρία), γι' αυτό δεν κάνει καμία εξοικονόμηση ενέργειας.

## 1. Ρόλος στο mesh — rank 0 άγκυρα

Όπως αναλύεται στο `03-mesh-routing.md`, η γέφυρα δεν επιλέγει ποτέ γονέα —
είναι πάντα `rank = 0`, το σταθερό σημείο αναφοράς όλου του δικτύου. Στέλνει
το δικό της beacon σε **σταθερό** interval 2000ms χωρίς trickle backoff
(`MESH_BRIDGE_BEACON_INTERVAL_MS`, `bridge_esp32.ino:200-205`) — δεν υπάρχει
λόγος οικονομίας αφού τροφοδοτείται μόνιμα.

## 2. WiFi STA — σύνδεση στο σπιτικό router

```c
WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
while (WiFi.status() != WL_CONNECTED) { delay(500); ... }
```
(`bridge_esp32.ino:160-163`) — **blocking** σύνδεση, αλλά μόνο στο `setup()`,
όχι στο `loop()`. Αν το WiFi κοπεί μετά την εκκίνηση, δεν υπάρχει ρητή
επανασύνδεση σε αυτόν τον κώδικα — η βιβλιοθήκη `WiFi.h` του Arduino-ESP32
core χειρίζεται εσωτερικά κάποιο reconnect, αλλά δεν υπάρχει δικιά μας
non-blocking retry λογική για το ίδιο το WiFi layer (σε αντίθεση με το MQTT
layer από πάνω, βλ. §4). SSID/password είναι hardcoded plaintext strings στο
firmware (`#define WIFI_SSID`/`WIFI_PASSWORD`, γραμμές 10-11) — καμία
μηχανή προαιρετικής ρύθμισης μέσω portal για τη γέφυρα (σε αντίθεση με το Pi,
που έχει captive portal — βλ. `09-setup-portal.md`).

## 3. MQTT Client — PubSubClient πάνω από TLS

- **Βιβλιοθήκη:** `PubSubClient` (Nick O'Leary) πάνω από `WiFiClientSecure`.
- **Θύρα/host:** `greenhouse.local:8883` (mDNS resolve στο Pi, βλ.
  `09-setup-portal.md` §Avahi), TLS listener του Mosquitto.
- **Πιστοποιητικό:** `net.setInsecure()` (`bridge_esp32.ino:166`) —
  **δεν γίνεται καμία επικύρωση πιστοποιητικού** από τη γέφυρα. Η γέφυρα
  εμπιστεύεται οποιονδήποτε server απαντήσει στο TLS handshake σε αυτό το
  host/port, βασιζόμενη αποκλειστικά στο ότι βρίσκεται στο ίδιο LAN + σωστό
  username/password. Αυτό είναι ρητή τεχνική επιλογή για self-signed
  πιστοποιητικά σε τοπικό δίκτυο (δες πλήρη ανάλυση trade-off στο
  `10-security.md`), όχι παράλειψη.
- **Buffer size:** `mqtt.setBufferSize(512)` (γραμμή 168) — το default του
  `PubSubClient` είναι μόλις 256 bytes, ανεπαρκές αν το topic string +
  payload ξεπεράσουν αυτό το όριο· μεγαλώθηκε προληπτικά.
- **Client ID:** `"gh-bridge-" + hex(ESP.getEfuseMac())` (γραμμή 45-46) —
  μοναδικό ανά φυσική συσκευή (βασισμένο στο eFuse MAC, μόνιμα καμένο στο
  silicon), ώστε ο broker να μην αποσυνδέσει τη γέφυρα λόγω duplicate
  client ID αν ποτέ τρέξουν δύο instances.
- **Credentials:** χρήστης `"app"` — ναι, η γέφυρα συνδέεται με το **ίδιο**
  MQTT username που χρησιμοποιεί και η εφαρμογή του κινητού (`MQTT_USER
  "app"`, `bridge_esp32.ino:16`), όχι με ξεχωριστό λογαριασμό "bridge"· ο
  κωδικός είναι hardcoded στο firmware (`MQTT_PASS`, γραμμή 17).

## 4. Non-blocking MQTT reconnect — κρίσιμη αρχιτεκτονική απόφαση

Υπάρχουν **δύο** ξεχωριστές συναρτήσεις reconnect, σκόπιμα:

- `reconnectMQTT()` (γραμμές 42-54) — **blocking** `while` loop με
  `delay(5000)` μεταξύ προσπαθειών. Καλείται **μόνο μία φορά**, μέσα στο
  `setup()`, πριν αρχίσει καν το mesh να κάνει beacon.
- `reconnectMQTTNonBlocking()` (γραμμές 59-71) — καλείται σε **κάθε**
  iteration του `loop()`. Ελέγχει αν έχουν περάσει 5000ms από την τελευταία
  προσπάθεια πριν ξαναδοκιμάσει, **χωρίς ποτέ να μπλοκάρει**.

**Γιατί έχει σημασία:** η γέφυρα είναι η άγκυρα rank-0 όλου του mesh. Αν το
`loop()` μπλόκαρε σε ένα blocking `while` περιμένοντας τον MQTT broker να
επιστρέψει (π.χ. κατά τη διάρκεια ενός restart του Mosquitto ή προσωρινού
δικτυακού προβλήματος), θα σταματούσε να στέλνει το δικό της beacon για όλη
τη διάρκεια της διακοπής — και ολόκληρο το mesh θα κατέρρεε ασύγχρονα
(όλοι οι rank-1 κόμβοι θα έχαναν τον γονέα τους, μετά οι rank-2, κ.ο.κ.),
ακόμα κι αν το radio πρόβλημα ήταν αποκλειστικά στο MQTT/Pi σκέλος. Το
`loop()` καλεί πάντα `meshSendBeaconNow()` ανεξάρτητα από την κατάσταση
MQTT (`bridge_esp32.ino:191-208`).

## 5. Zone lookup βάσει `origin_mac`, όχι άμεσου αποστολέα

```c
int idx = meshTrustedIndex(pkt.origin_mac);   // ΟΧΙ info->src_addr
```
(`bridge_esp32.ino:90`). Αυτή είναι η **μοναδική** λειτουργική αλλαγή που
έφερε το multi-hop mesh στη γέφυρα σε σχέση με το παλιό star-topology
σχέδιο: πριν, ο ESP-NOW `src_addr` ήταν πάντα ο πραγματικός αισθητήρας
(κάθε κόμβος έστελνε απευθείας). Τώρα, το `src_addr` μπορεί να είναι ένας
ενδιάμεσος relay — το `origin_mac` μέσα στο ίδιο το `MeshDataPacket` είναι
το μόνο αξιόπιστο στοιχείο για ποιος πραγματικά μέτρησε.

## 6. Δημοσίευση MQTT — topics, retain, QoS

Για κάθε έγκυρο πακέτο (ranked, de-dup-checked, γνωστό `origin_mac`):

```
greenhouse/<zone>/air/temperature     → "%.1f"
greenhouse/<zone>/air/humidity        → "%.1f"
greenhouse/<zone>/soil/moisture       → "%.1f"
greenhouse/nodes/<MAC-hex>/status     → "online"
```

Όλα με **`retain = true`** (`mqttPublish(topic, payload, true)`,
`bridge_esp32.ino:113-126`). Πριν από αυτή τη σχεδιαστική αλλαγή (mesh
relay session), η δημοσίευση γινόταν χωρίς retain — αποτέλεσμα: μετά από
κάθε restart του broker, οι κάρτες ζώνης στην εφαρμογή έμεναν άδειες μέχρι
την επόμενη πραγματική μέτρηση (έως 5 δευτερόλεπτα, αλλά αισθητό UX κενό).
Με retain, ο broker κρατά **το τελευταίο** μήνυμα ανά topic και το στέλνει
αμέσως σε κάθε νέο subscriber (όπως η εφαρμογή στο restart/reconnect).

QoS: `mqtt.publish()` του `PubSubClient` χωρίς ρητή παράμετρο QoS
χρησιμοποιεί πάντα **QoS 0** (fire-and-forget, καμία εγγύηση παράδοσης σε
επίπεδο MQTT) — αποδεκτό εδώ γιατί ο ίδιος ο μηχανισμός mesh+retain παρέχει
ήδη επαρκή αξιοπιστία στην πράξη (νέα μέτρηση κάθε 5s, οπότε η απώλεια ενός
μηνύματος αντικαθίσταται σχεδόν αμέσως).

## 7. Ανίχνευση offline κόμβων

Ανάλυση αλγορίθμου στο `03-mesh-routing.md §9`. Σημειώνεται εδώ ότι ο
έλεγχος γίνεται **μόνο όταν η γέφυρα είναι ήδη συνδεδεμένη στο MQTT**
(`if (!mqtt.connected()) return;`, `bridge_esp32.ino:133`) — σκόπιμα, ώστε
να μη "χαθεί" μια μετάβαση online→offline απλά επειδή ο broker ήταν
προσωρινά κάτω τη στιγμή που θα έπρεπε να δημοσιευτεί.
