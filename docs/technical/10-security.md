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

## 4. TLS layer #2 — Εφαρμογή ↔ τοπικός Mosquitto (accept-all + άχρηστο fingerprint)

`app/lib/connection/mqtt_connection.dart:70`:
```dart
client.onBadCertificate = (Object _) => true;  // accept self-signed; pin in Slice 5
```
Η εφαρμογή **επίσης** δέχεται οποιοδήποτε πιστοποιητικό, ανεξάρτητα από
περιεχόμενο. Αξιοσημείωτο: το `/pair` endpoint **επιστρέφει** το SHA-256
fingerprint του πραγματικού server certificate
(`ConnectionConfig.tlsFingerprint`, `portal.py:210`), το οποίο **θα
μπορούσε** να χρησιμοποιηθεί για πραγματικό certificate pinning — αλλά
**δεν χρησιμοποιείται ποτέ** σε κανένα σημείο ελέγχου· απλά αποθηκεύεται.
Το σχόλιο στον κώδικα ("pin in Slice 5") δείχνει ότι αυτό ήταν
προγραμματισμένο αλλά ποτέ δεν υλοποιήθηκε — γνωστό, ανοιχτό backlog item
(`HANDOFF.md`: "`/pair` and `/api/history*` are unauthenticated. Fine for
LAN-only/thesis use").

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
- **TLS listener (8883):** `password_file`, δύο accounts: `app` (χρήστης
  εφαρμογή/γέφυρα — μοιράζονται το ίδιο username, δες `04-bridge-gateway.md §3`),
  μοναδικό password ανά μονάδα Pi (παράγεται στο `first_boot.sh`).
- **Δεν υπάρχει ACL** (access control list) στο Mosquitto config — κάθε
  authenticated client (είτε γέφυρα είτε εφαρμογή) μπορεί να διαβάσει/
  γράψει **οποιοδήποτε** topic, όχι μόνο τα δικά του. Αποδεκτό εδώ γιατί
  υπάρχουν μόνο δύο πραγματικοί client τύποι και αμφότεροι είναι
  αξιόπιστοι (δικά μας firmware/app, όχι πολλαπλοί ανεξάρτητοι πελάτες).

## 7. Μοναδικότητα secrets ανά φυσική μονάδα

Αναλύεται πλήρως στο `09-setup-portal.md §5-6`. Σύνοψη:

| Secret | Πού παράγεται | Μοναδικό ανά μονάδα; |
|---|---|---|
| TLS CA + server cert/key | `gen_certs.sh`, πρώτο boot | Ναι — νέο RSA-2048 ζευγάρι |
| MQTT password (χρήστης `app`) | `first_boot.sh` | Ναι — `openssl rand -base64 21` |
| OS password (χρήστης `pi`) | `install.sh` implicit (Pi Imager αρχικό, αλλάζει σε τυχαίο μετά) | Ναι |
| AP SSID | `ap_up.sh`, runtime από MAC | Ναι |
| ESP-NOW PMK/LMK | `mesh_config.h`, compile-time constant | **Όχι** — ίδιο σε όλες τις συσκευές (compiled στο firmware, δεν παράγεται per-unit) |
| HiveMQ Cloud credentials | `install.sh` hardcoded | **Όχι** — ένας κοινός λογαριασμός HiveMQ για όλο το fleet (single-tenant model, δες σημείωση §8) |

## 8. Γνωστά, ρητά αποδεκτά όρια εμβέλειας

Άμεσα από το `HANDOFF.md` backlog, όχι εικασία:
- `/pair` και `/api/history*` χωρίς καμία αυθεντικοποίηση πέρα από
  χρονικό παράθυρο/δικτυακή τοποθεσία — αποδεκτό για LAN-only/thesis
  χρήση, θα χρειαζόταν PIN/QR/token πριν από δημόσια ανάπτυξη.
- Ένας κοινός λογαριασμός HiveMQ Cloud για **όλο** το fleet μονάδων
  (single-tenant μοντέλο) — δεν υπάρχει ακόμα per-customer διαχωρισμός/
  device registry σε πολλαπλούς πελάτες.
- Κοινό ESP-NOW PMK/LMK δικτύου-ευρείας (όχι per-pair) — υπερασπίζεται
  ενάντια σε εξωτερικό εισβολέα, όχι ενάντια σε φυσική κλοπή/ανάλυση μιας
  μονάδας.
- Καμία proof-of-possession επαλήθευση στο `/pair` πέρα από το χρονικό
  παράθυρο — όποιος είναι στο LAN μέσα στα πρώτα 600 δευτερόλεπτα μπορεί
  να ζευγαρώσει.
