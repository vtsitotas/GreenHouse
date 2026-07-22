# 10 — Ασφάλεια: Πλήρης Χάρτης Κρυπτογράφησης & Αυθεντικοποίησης

Συγκεντρωτική εικόνα κάθε "λεπτού σημείου" ασφάλειας σε όλο το σύστημα,
με τι **πραγματικά** κάνει ο κώδικας σε κάθε ζεύξη — όχι τι θα ήταν ιδανικό.

## 1. Χάρτης ζεύξεων

```
[Αισθητήρας] --ESP-NOW/AES-128-CTR (PMK/LMK)--> [Γέφυρα]
[Γέφυρα]     --MQTT/TLS 1.2, setInsecure()-----> [Mosquitto :8883]
[Εφαρμογή]   --MQTT/TLS, onBadCertificate=true--> [Mosquitto :8883]
[Mosquitto]  --MQTT/TLS 1.2, tls_set (validated)-> [HiveMQ Cloud :8883]
[Εφαρμογή]   --MQTT/TLS (ίδιο accept-all)-------> [HiveMQ Cloud :8883]
[Εφαρμογή]   --HTTP (χωρίς TLS)------------------> [portal.py :80] (μόνο LAN)
[Κάμερα]     --HTTP (χωρίς TLS)------------------> [cam_bridge.py :8090] (μόνο LAN)
```

## 2. ESP-NOW layer — τι κρυπτογραφείται και τι όχι

Πλήρης ανάλυση στο `02-esp-now-protocol.md §Layer 2`. Σύνοψη:
- **Beacons** (ανακάλυψη γειτόνων/rank): πάντα plaintext — hardware
  περιορισμός (broadcast frames δεν κρυπτογραφούνται στο ESP-NOW),
  όχι επιλογή. Αποκαλύπτουν μόνο `{MAC, rank, seq}` — ποτέ πραγματική
  μέτρηση.
- **Δεδομένα αισθητήρων** (unicast προς γονέα): πάντα κρυπτογραφημένα,
  AES-128-CTR, κοινό δίκτυο-ευρείας PMK/LMK ζευγάρι
  (`mesh_config.h:45-50`) — **ίδιο κλειδί σε όλες τις συσκευές του
  δικτύου**, όχι ξεχωριστό ανά ζεύγος κόμβων.
- **Trust gate**: μόνο MAC διευθύνσεις μέσα στο `TRUSTED_NODES[]` γίνονται
  ποτέ αποδεκτές ως πηγή δεδομένων ή υποψήφιος γονέας
  (`meshTrustedIndex()`, ελέγχεται σε κάθε λήψη beacon/data).

**Ρητά τεκμηριωμένο trade-off** (σχόλιο `mesh_config.h:42-44` και design
spec §Non-goals): το κοινό-δικτύου κλειδί υπερασπίζεται ενάντια σε έναν
**ξένο δέκτη κοντά** που προσπαθεί να κάνει inject ή να διαβάσει δεδομένα —
**δεν** υπερασπίζεται ενάντια σε εξαγωγή του κλειδιού από μια φυσικά
κλεμμένη/παραβιασμένη συσκευή (θα εξέθετε όλο το δίκτυο). Ρητά αποδεκτό ως
εκτός εμβέλειας για διπλωματική εργασία.

## 3. TLS layer #1 — Γέφυρα ↔ τοπικός Mosquitto (`setInsecure`)

`bridge_esp32.ino:166`: `net.setInsecure();` — η βιβλιοθήκη
`WiFiClientSecure` κάνει το TLS handshake (κρυπτογράφηση καναλιού
πλήρως ενεργή) αλλά **παραλείπει εντελώς την επικύρωση της αλυσίδας
πιστοποιητικού**. Πρακτικά σημαίνει: η γέφυρα θα δεχόταν TLS handshake
από *οποιονδήποτε* server σε αυτό το host:port, ακόμα κι αν παρουσίαζε
τελείως άσχετο πιστοποιητικό. Η εμπιστοσύνη εδώ βασίζεται σε:
- Το γεγονός ότι η γέφυρα βρίσκεται φυσικά μέσα στο ίδιο LAN.
- Username/password authentication στο MQTT layer (πάνω από το TLS).

Αυτό είναι αποδεκτό επειδή δεν υπάρχει βολικός τρόπος να "τσεκάρει" ο
μικροελεγκτής ένα per-unit αυτο-υπογεγραμμένο πιστοποιητικό χωρίς να το
έχει προ-εγκατεστημένο (και δεν υπάρχει μηχανισμός provisioning
πιστοποιητικού *στη γέφυρα* — μόνο στην εφαρμογή, μέσω `/pair`).

## 4. TLS layer #2 — Εφαρμογή ↔ τοπικός Mosquitto (certificate pinning) — ✅ ενεργό

`app/lib/connection/mqtt_connection.dart`'s `onBadCertificate` callback
υπολογίζει το SHA-256 του DER του πιστοποιητικού που παρουσιάζει ο server
και το συγκρίνει με το αποθηκευμένο `ConnectionConfig.tlsFingerprint` (το
οποίο έρχεται από το `/pair` response, `portal.py`'s `_pairing_payload()`)
— μόνο σε ταίριασμα επιστρέφει `true`. Αν το fingerprint είναι κενό (π.χ.
`/pair` απάντησε πριν παραχθούν certs) η σύνδεση **απορρίπτεται** (fail
closed), όχι blind-accept. Αυτό έκλεινε ένα πραγματικό MITM κενό: πριν,
οποιοσδήποτε παρουσίαζε *οποιοδήποτε* πιστοποιητικό γινόταν δεκτός.

Επιπλέον, τα ίδια τα credentials δεν επιστρέφονται πια χωρίς
αυθεντικοποίηση: το `GET /pair` επιστρέφει μόνο `{"found": true}` — το
πραγματικό secret-fetch μετακινήθηκε στο PIN-gated `POST /pair/confirm`
(§6 παρακάτω, και
`docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md`).

## 5. TLS layer #3 — Mosquitto ↔ HiveMQ Cloud (πλήρης επικύρωση)

Μοναδική ζεύξη στο σύστημα με **σωστή, πλήρη** TLS επικύρωση:
```python
remote.tls_set(ca_certs='/etc/ssl/certs/ca-certificates.crt',
                tls_version=ssl.PROTOCOL_TLSv1_2)
```
(`hivemq_bridge.py:83`). Χρησιμοποιεί το **δημόσιο** σύστημα CA trust
store του λειτουργικού (τα ίδια root certificates που εμπιστεύεται κάθε
browser) για να επικυρώσει το πιστοποιητικό του HiveMQ Cloud, το οποίο
είναι πραγματικά υπογεγραμμένο από δημόσια αναγνωρισμένη CA (όχι
αυτο-υπογεγραμμένο). Έχει νόημα η διαφορά: αυτή η ζεύξη περνά από το
δημόσιο Internet, ενώ οι προηγούμενες μένουν εντός LAN.

## 6. Αυθεντικοποίηση MQTT

- **Loopback (1883):** `allow_anonymous true` — καμία αυθεντικοποίηση,
  προστασία αποκλειστικά μέσω network binding (`127.0.0.1` only).
- **TLS listener (8883):** `password_file`, δύο **ξεχωριστοί** λογαριασμοί,
  μοναδικό password ανά μονάδα Pi (παράγονται στο `first_boot.sh`):
  `app` (η εφαρμογή) και `bridge` (bridge_esp32 — δικός του λογαριασμός
  πλέον, δεν μοιράζεται πια το `app`, βλ. `04-bridge-gateway.md §3`).
- **ACL** (`pi/mosquitto/acl`, `acl_file` στο `mosquitto.conf`): `bridge`
  περιορίζεται σε **publish-only** στα sensor/node-status topics
  (`greenhouse/+/air/temperature`, `.../air/humidity`, `.../soil/moisture`,
  `greenhouse/nodes/+/status`) — ταιριάζει ακριβώς με ό,τι δημοσιεύει στην
  πράξη (`bridge_esp32.ino`, ποτέ δεν κάνει subscribe σε τίποτα). Μια
  παραβιασμένη/spoofed γέφυρα δεν μπορεί πλέον να στείλει εντολές σε
  actuators, να αλλάξει rules, ή να διαβάσει οτιδήποτε. Το `app` παραμένει
  `readwrite greenhouse/#` (πλήρης πρόσβαση — αξιόπιστος end-user client).

## 7. Μοναδικότητα secrets ανά φυσική μονάδα

Αναλύεται πλήρως στο `09-setup-portal.md §5-6`. Σύνοψη:

| Secret | Πού παράγεται | Μοναδικό ανά μονάδα; |
|---|---|---|
| TLS CA + server cert/key | `gen_certs.sh`, πρώτο boot | Ναι — νέο RSA-2048 ζευγάρι |
| MQTT password (χρήστης `app`) | `first_boot.sh` | Ναι — `openssl rand -base64 21` |
| MQTT password (χρήστης `bridge`) | `first_boot.sh` | Ναι — ίδια μέθοδος, ξεχωριστό password από το `app` |
| PIN ζευγαρώματος (`POST /pair/confirm`) | `first_boot.sh` | Ναι — 6-ψήφιο, `/dev/urandom` |
| OS password (χρήστης `pi`) | `install.sh` implicit (Pi Imager αρχικό, αλλάζει σε τυχαίο μετά) | Ναι |
| AP SSID | `ap_up.sh`, runtime από MAC | Ναι |
| ESP-NOW PMK/LMK | `mesh_config.h`, compile-time constant | **Όχι** — ίδιο σε όλες τις συσκευές (compiled στο firmware, δεν παράγεται per-unit) |
| HiveMQ Cloud credentials | χειροκίνητα σε `/etc/greenhouse/hivemq.json` (`install.sh` γράφει μόνο placeholder template πλέον — δες finding A1) | **Όχι** — ένας κοινός λογαριασμός HiveMQ για όλο το fleet (single-tenant model, δες σημείωση §8) |

## 8. Γνωστά, ρητά αποδεκτά όρια εμβέλειας

Άμεσα από το `HANDOFF.md` backlog, όχι εικασία:
- `/api/history*` χωρίς καμία αυθεντικοποίηση πέρα από δικτυακή τοποθεσία
  — αποδεκτό για LAN-only/thesis χρήση (μόνο read-only ιστορικά δεδομένα,
  όχι credentials). `/pair` πλέον **δεν** ανήκει εδώ — βλ. παρακάτω.
- Ένας κοινός λογαριασμός HiveMQ Cloud για **όλο** το fleet μονάδων
  (single-tenant μοντέλο) — δεν υπάρχει ακόμα per-customer διαχωρισμός/
  device registry σε πολλαπλούς πελάτες.
- Κοινό ESP-NOW PMK/LMK δικτύου-ευρείας (όχι per-pair) — υπερασπίζεται
  ενάντια σε εξωτερικό εισβολέα, όχι ενάντια σε φυσική κλοπή/ανάλυση μιας
  μονάδας.
- ✅ **Έγινε:** το `/pair` πλέον απαιτεί proof-of-possession — `GET /pair`
  επιστρέφει μόνο `{"found": true}`, τα credentials δίνονται μόνο μέσω
  `POST /pair/confirm` με σωστό PIN (5 προσπάθειες πριν το lockout). Το
  χρονικό παράθυρο των 600s παραμένει επιπλέον επίπεδο άμυνας στο ίδιο
  `GET /pair`, αμετάβλητο. Ανοιχτό παραμένει μόνο το Goal 4 του σχετικού
  spec (μόνιμη διαθεσιμότητα σε AP mode χωρίς restart) — βλ. `TODO.md`.
