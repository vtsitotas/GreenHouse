# 10 — Ασφάλεια: Πλήρης Χάρτης Κρυπτογράφησης & Αυθεντικοποίησης

Συγκεντρωτική εικόνα κάθε "λεπτού σημείου" ασφάλειας σε όλο το σύστημα,
με τι **πραγματικά** κάνει ο κώδικας σε κάθε ζεύξη — όχι τι θα ήταν ιδανικό.

## 1. Χάρτης ζεύξεων

```
[Αισθητήρας] --ESP-NOW/AES-128-CTR (PMK/LMK)------> [Γέφυρα]
[Γέφυρα]     --UART 3.3V (φυσικό καλώδιο)---------> [serial_bridge.py] → :1883 loopback
[Γέφυρα-WiFi]--MQTT/TLS 1.2, setInsecure()--------> [Mosquitto :8883]  (fallback firmware)
[Εφαρμογή]   --MQTT/TLS + certificate pinning-----> [Mosquitto :8883]
[Mosquitto]  --MQTT/TLS 1.2, tls_set (validated)--> [HiveMQ Cloud :8883]
[Εφαρμογή]   --MQTT/TLS + pinning----------------> [HiveMQ Cloud :8883]
[Εφαρμογή]   --HTTP + PIN / Bearer token---------> [portal.py :80]        (μόνο LAN)
[Εφαρμογή]   --HTTP + CAM_TOKEN (?token=)--------> [cam_esp32 /stream]    (μόνο LAN)
[Κάμερα]     --HTTP + CAM_TOKEN (X-Cam-Token)----> [cam_bridge.py :8090]  (μόνο LAN)
[cam_bridge] --HTTP + CAM_TOKEN (?token=)--------> [cam_esp32 /capture, /event]
```

**Κανένα HTTP endpoint σε αυτό το σύστημα δεν είναι πλέον χωρίς
αυθεντικοποίηση.** Κάθε ένα από τα παραπάνω αποτυγχάνει *κλειστά* (fail
closed) όταν λείπει το αντίστοιχο μυστικό — ένα μη-provisioned unit αρνείται,
δεν εξυπηρετεί.

**Και η μεταφορά είναι πλέον κρυπτογραφημένη** για τα endpoints που μεταφέρουν
μυστικά: το portal σερβίρει **HTTPS στη θύρα 8443** με το ίδιο per-unit
πιστοποιητικό που χρησιμοποιεί ο broker (`gen_certs.sh` → `/etc/greenhouse/
portal.{crt,key}`), και η εφαρμογή κάνει **certificate pinning** με το ίδιο
fingerprint που ήδη καρφιτσώνει για το MQTT (`app/lib/utils/cert_pinning.dart`,
κοινό και για τις δύο ζεύξεις). Η θύρα 80 παραμένει plaintext **μόνο** για το
captive portal — το popup του λειτουργικού δεν πυροδοτείται πάνω από TLS και
ένα self-signed cert εκεί θα έσπαγε τη ροή πρώτης εγκατάστασης.

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

## 6α. HTTP αυθεντικοποίηση (portal + κάμερα)

Προστέθηκε στο πέρασμα σκλήρυνσης 2026-07-28. Πριν από αυτό, τρία HTTP
endpoints ήταν εντελώς ανοιχτά σε οποιονδήποτε στο LAN/hotspot.

| Endpoint | Μηχανισμός | Πού ζει το μυστικό |
|---|---|---|
| `POST /cam/frame` (`cam_bridge.py:8090`) | `X-Cam-Token` header ή `?token=` | `/etc/greenhouse/cam_token.txt` ↔ flashed `CAM_TOKEN` |
| `GET /api/history`, `/api/history/series` (`portal.py:80`) | `Authorization: Bearer <api_token>` ή `?token=` | `device.json` → app μέσω `/pair/confirm` |
| `GET /stream` (`cam_esp32`) | `?token=` | ίδιο `CAM_TOKEN`, app μέσω `/pair/confirm` |
| `GET /capture`, `GET\|DELETE /event/<id>` (`cam_esp32`) | `?token=` | ίδιο `CAM_TOKEN` (ήδη υπήρχε) |
| `POST /pair/confirm` (`portal.py:80`) | 6-ψήφιο PIN, **constant-time** σύγκριση | `device.json`, ετικέτα συσκευής |

**Το σοβαρότερο κενό που έκλεισε — αλυσίδα επίθεσης στο `POST /cam/frame`:**
το endpoint δεχόταν snapshot από οποιονδήποτε, και το `_update_heartbeat()`
όριζε ως «IP της κάμερας» τη διεύθυνση του αποστολέα. Συνέπεια: ένας
επιτιθέμενος στο LAN έστελνε **ένα** POST και (1) γινόταν «η κάμερα» για το
Pi, (2) το Pi έστελνε στη συνέχεια το `CAM_TOKEN` **στον ίδιο** ως query
param σε κάθε `/capture` και `/event/<id>` — δηλαδή του παρέδιδε το token που
εξουσιοδοτεί το `DELETE /event/<id>`, άρα τη δυνατότητα **καταστροφής των
αποθηκευμένων στιγμιότυπων κίνησης** στην πραγματική κάμερα, (3) οι δικές του
εικόνες προωθούνταν στην εφαρμογή ως live/event frames, και (4) κάθε
πλαστό frame μπορούσε να πυροδοτήσει motion event + push notification + εγγραφή
στη βάση. Καλύπτεται τώρα από 7 στοχευμένα tests (`test_cam_bridge.py`), τα
οποία επαληθεύτηκε ότι **αποτυγχάνουν** αν αφαιρεθεί ο έλεγχος.

### Κρυπτογράφηση μεταφοράς (HTTPS στο portal)

Τα tokens παραπάνω σταματούν τη *μη εξουσιοδοτημένη πρόσβαση*. Για να
σταματήσει και η *παθητική υποκλοπή*, το portal σερβίρει τα ίδια endpoints και
πάνω από TLS:

| | Θύρα | Κρυπτογράφηση | Ποιος το χρησιμοποιεί |
|---|---|---|---|
| Captive portal (`/`, `/api/scan`, `/connect`) | 80 | ❌ plaintext **σκόπιμα** | browser του τηλεφώνου, πρώτη εγκατάσταση |
| `/pair` (έλεγχος ύπαρξης, χωρίς μυστικά) | 80 + 8443 | προαιρετικά | εφαρμογή, discovery |
| `/pair/confirm`, `/api/history*` | 8443 | ✅ TLS + pinning | εφαρμογή |

- **Πιστοποιητικό:** το ίδιο per-unit ζεύγος με τον broker (`gen_certs.sh`
  γράφει ένα αντίγραφο αναγνώσιμο από τον `pi` στο `/etc/greenhouse/portal.*`),
  ώστε το fingerprint που η εφαρμογή ήδη καρφιτσώνει να καλύπτει **και** το
  portal — μία ταυτότητα ανά μονάδα.
- **Συμβατότητα:** η εφαρμογή δοκιμάζει HTTPS πρώτα και πέφτει πίσω σε HTTP αν
  το Pi δεν έχει ακόμα ενημερωθεί. Αυτό είναι πραγματική διαδρομή υποβάθμισης,
  γι' αυτό υπάρχει διακόπτης επιβολής: `touch /etc/greenhouse/require_https`
  κάνει τα plaintext αντίγραφα των ευαίσθητων endpoints να απαντούν 403.
  **Δεν** είναι ενεργός εξ ορισμού — θα κλείδωνε έξω κάθε τηλέφωνο που δεν έχει
  ακόμα ενημερωμένη εφαρμογή. Το `selftest.sh` το θυμίζει.
- **Ρητό όριο — first-contact MITM:** τη *στιγμή* του ζευγαρώματος η εφαρμογή
  δεν έχει ακόμα το fingerprint (βρίσκεται μέσα στην απάντηση), οπότε δέχεται
  το πιστοποιητικό χωρίς επαλήθευση αυτή τη μία φορά. Αυτό εξουδετερώνει τον
  **παθητικό** παρακολουθητή (το ρεαλιστικό σενάριο σε κοινό WiFi) αλλά όχι
  έναν **ενεργό** MITM. Κάθε επόμενο αίτημα είναι πλήρως pinned. Η διαδρομή QR
  κλείνει και αυτό το κενό, γιατί μεταφέρει το fingerprint εκτός δικτύου.

## 6β. Sandboxing των υπηρεσιών (systemd)

Κάθε Python service τρέχει ως `pi` (ποτέ root) με πλήρες σετ περιορισμών:
`NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=strict`,
`ProtectKernelTunables/Modules/Logs`, `ProtectControlGroups`, `ProtectClock`,
`ProtectHostname`, `ProtectProc=invisible`, `RestrictNamespaces`,
`RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`,
`CapabilityBoundingSet=` (κενό — καμία capability),
`RestrictAddressFamilies` (χωρίς `AF_PACKET`), `SystemCallFilter=@system-service`,
`UMask=0077`, και `ReadWritePaths` περιορισμένο στο ελάχιστο.

Δύο **σκόπιμες** εξαιρέσεις, τεκμηριωμένες μέσα στα ίδια τα unit files:
- `greenhouse-serial-bridge`: **χωρίς** `PrivateDevices` — θα έκρυβε το
  `/dev/serial0`, που είναι ακριβώς ο λόγος ύπαρξης της υπηρεσίας.
- `greenhouse-portal`: **χωρίς** `NoNewPrivileges`, `CapabilityBoundingSet=`
  και `SystemCallFilter` — χρειάζεται setuid `sudo` (nmcli/reboot) και
  `CAP_NET_BIND_SERVICE` για τη θύρα 80. Παίρνει όλα τα υπόλοιπα.
  (`RestrictSUIDSGID` είναι ασφαλές εδώ: εμποδίζει τη *δημιουργία* setuid
  αρχείων, όχι την *εκτέλεση* του υπάρχοντος `sudo`.)

## 7. Μοναδικότητα secrets ανά φυσική μονάδα

Αναλύεται πλήρως στο `09-setup-portal.md §5-6`. Σύνοψη:

| Secret | Πού παράγεται | Μοναδικό ανά μονάδα; |
|---|---|---|
| TLS CA + server cert/key | `gen_certs.sh`, πρώτο boot | Ναι — νέο RSA-2048 ζευγάρι |
| MQTT password (χρήστης `app`) | `first_boot.sh` | Ναι — `openssl rand -base64 21` |
| MQTT password (χρήστης `bridge`) | `first_boot.sh` | Ναι — ίδια μέθοδος, ξεχωριστό password από το `app` |
| PIN ζευγαρώματος (`POST /pair/confirm`) | `first_boot.sh` | Ναι — 6-ψήφιο, `/dev/urandom` |
| API token (`/api/history*`) | `first_boot.sh` (νέα units), `install.sh` backfill (υπάρχοντα) | Ναι — 32 χαρακτήρες, `openssl rand` |
| `CAM_TOKEN` (κάμερα ↔ Pi, και τις δύο κατευθύνσεις) | χειροκίνητα σε `/etc/greenhouse/cam_token.txt` + flashed `secrets.h` | **Όχι** — κοινό ανά δίκτυο, χειροκίνητος συγχρονισμός |
| OS password (χρήστης `pi`) | `install.sh` implicit (Pi Imager αρχικό, αλλάζει σε τυχαίο μετά) | Ναι |
| AP SSID | `ap_up.sh`, runtime από MAC | Ναι |
| ESP-NOW PMK/LMK | `mesh_config.h`, compile-time constant | **Όχι** — ίδιο σε όλες τις συσκευές (compiled στο firmware, δεν παράγεται per-unit) |
| HiveMQ Cloud credentials | χειροκίνητα σε `/etc/greenhouse/hivemq.json` (`install.sh` γράφει μόνο placeholder template πλέον — δες finding A1) | **Όχι** — ένας κοινός λογαριασμός HiveMQ για όλο το fleet (single-tenant model, δες σημείωση §8) |

## 8. Γνωστά, ρητά αποδεκτά όρια εμβέλειας

Τι **δεν** καλύπτει το σύστημα ακόμα και μετά το πέρασμα σκλήρυνσης — δηλωμένο
ρητά, γιατί «ασφαλές» χωρίς αναφορά σε μοντέλο απειλής δεν σημαίνει τίποτα.

1. **First-contact MITM στο ζευγάρωμα — μόνο στη διαδρομή mDNS discovery.**
   Αν το fingerprint είναι ήδη γνωστό εκτός δικτύου (**QR**, ή χειροκίνητη
   εισαγωγή στο Advanced), η σύνδεση ζευγαρώματος είναι **pinned**: ένας MITM
   με δικό του πιστοποιητικό απορρίπτεται *πριν* σταλεί το PIN, και η εφαρμογή
   δείχνει ρητή προειδοποίηση ασφαλείας. Το κενό παραμένει **μόνο** όταν
   ζευγαρώνεις μέσω mDNS χωρίς QR, γιατί τότε δεν υπάρχει τίποτα να
   επαληθευτεί. Ένα 6-ψήφιο PIN **δεν** μπορεί να το κλείσει: οποιαδήποτε
   απόδειξη γνώσης κουβαλούσε θα ήταν offline brute-forceable από μία μόνο
   καταγεγραμμένη ανταλλαγή (10^6 δοκιμές). Η σωστή απάντηση είναι η
   out-of-band παράδοση του fingerprint — δηλαδή το QR, το οποίο η οθόνη
   ζευγαρώματος πλέον συστήνει ρητά ως το ασφαλέστερο.
2. **Τα *περιεχόμενα* των καρέ της κάμερας παραμένουν plaintext.** Το
   *διαπιστευτήριο* όμως **όχι πια**: το `POST /cam/frame` δέχεται πλέον
   **υπογεγραμμένα** αιτήματα — `HMAC-SHA256(CAM_TOKEN, timestamp + sha256(body))`
   — οπότε το token δεν ταξιδεύει ποτέ στο δίκτυο, η υπογραφή είναι δεσμευμένη
   σε συγκεκριμένο σώμα και χρονική στιγμή, και τα replays απορρίπτονται
   (`_seen_signatures`). Ένας παθητικός παρακολουθητής βλέπει τα JPEG αλλά
   **δεν** μπορεί πλέον να ανακτήσει το `CAM_TOKEN` ούτε να επαναλάβει αίτημα.
   Η κρυπτογράφηση των ίδιων των καρέ θα απαιτούσε TLS στον ESP32, άρα
   provisioning του πιστοποιητικού μέσα στο firmware — πραγματική αλλαγή
   σχεδιασμού. Ο διακόπτης `/etc/greenhouse/no_unsigned_cam` απορρίπτει
   εντελώς τη διαδρομή bearer-token μόλις κάθε κάμερα ξαναφλασαριστεί.
3. **Κοινό `CAM_TOKEN` ανά μονάδα-και-κάμερα**, χειροκίνητα συγχρονισμένο με το
   flashed firmware. Πλέον **παράγεται τυχαία per-unit** (`first_boot.sh`) αντί
   να αντιγράφεται το δημόσιο placeholder του repo, και το `selftest.sh`
   αποτυγχάνει αν κάποια μονάδα εξακολουθεί να το χρησιμοποιεί — αλλά η
   εναλλαγή του απαιτεί reflash της κάμερας.
4. **Κοινό ESP-NOW PMK/LMK** δικτύου-ευρείας (όχι per-pair) — υπερασπίζεται
   ενάντια σε εξωτερικό εισβολέα, όχι ενάντια σε φυσική κλοπή/εξαγωγή κλειδιού
   από μια μονάδα. Per-node κλειδιά θα απαιτούσαν reflash όλου του στόλου και
   απορρίπτονται ρητά στο design spec (Non-goals).
5. **Ένας κοινός λογαριασμός HiveMQ Cloud** για όλο το fleet (single-tenant)
   — δεν υπάρχει per-customer διαχωρισμός/device registry.
6. **`sudo nmcli` χωρίς password για τον `pi`** — απαραίτητο για τη ροή WiFi
   setup του portal, αλλά ευρύ: οποιαδήποτε παραβίαση *οποιασδήποτε* υπηρεσίας
   που τρέχει ως `pi` αποκτά αυτό το δικαίωμα. Μετριάζεται από το §6β sandboxing
   και από το ότι πλέον κανένα endpoint δεν είναι ανοιχτό.
7. **Τα secrets που διέρρευσαν σε παλιά commits παραμένουν στο git history**
   (επιβεβαιωμένο: commit `c0383b3`, πραγματικό WiFi + MQTT password). Η δομική
   μετακίνηση σε gitignored `secrets.h` **δεν** τα ακύρωσε. Πλέον υπάρχει
   αυτοματοποίηση για το μισό του προβλήματος: **`pi/scripts/rotate_secrets.sh`**
   εναλλάσσει με μία εντολή κάθε μυστικό που ανήκει στο Pi (MQTT passwords
   `app`+`bridge`, API token, PIN, cam token, και το ίδιο το TLS keypair).
   Ό,τι ζει **εκτός** του Pi παραμένει χειροκίνητο εξ ανάγκης — WiFi password
   στο router, HiveMQ password στην κονσόλα τους. Πλήρης λίστα βημάτων και
   διαδικασία καθαρισμού του git history: **`SECURITY.md`**.
8. **Φυσική πρόσβαση στο Pi.** Η κάρτα SD δεν είναι κρυπτογραφημένη· όποιος την
   κρατά διαβάζει `device.json` και τα ιδιωτικά κλειδιά TLS.

**Κλεισμένα σε αυτό το πέρασμα** (ήταν σε αυτή τη λίστα):
- ✅ `/api/history*` — πλέον bearer-token gated (§6α).
- ✅ `POST /cam/frame` — πλέον token-gated + rate-limited· ήταν η σοβαρότερη
  τρύπα (§6α).
- ✅ `/stream` της κάμερας — πλέον token-gated· ήταν το τελευταίο ανοιχτό
  endpoint (`IMPROVEMENTS.md §Α5`).
- ✅ `/pair` — proof-of-possession μέσω PIN, τώρα με **constant-time**
  σύγκριση (πριν, το `!=` σε string διέρρεε το σωστό πρόθεμα μέσω χρονισμού).
- ✅ Mosquitto ACL — κάλυπτε 4 από τα 6 topics που δημοσιεύει η γέφυρα· τα
  `/battery` και `/mesh` απορρίπτονταν σιωπηλά.
- ✅ **Plaintext HTTP στα ευαίσθητα endpoints** — πλέον TLS στο 8443 με
  certificate pinning, συν προαιρετικό διακόπτη επιβολής (§6α). Παραμένει
  ανοιχτό μόνο το first-contact σενάριο (§8.1) και η ζεύξη της κάμερας (§8.2).
- ✅ **Global pairing lockout ως DoS** — το `_pair_locked` ήταν καθολικό και
  latch-until-restart, οπότε **οποιοσδήποτε** στο δίκτυο μπορούσε να μπλοκάρει
  μόνιμα το ζευγάρωμα του ιδιοκτήτη με 5 λάθος PIN. Τώρα per-IP, με
  αυτόματη λήξη (15 λεπτά), φραγμένο πίνακα κατάστασης, και καθολικό backstop
  πολύ ψηλότερα για κατανεμημένες προσπάθειες. Το `X-Forwarded-For` **δεν**
  γίνεται δεκτό (δεν υπάρχει proxy μπροστά) ώστε να μην παρακάμπτεται το όριο.
- ✅ **Δημόσιο placeholder `CAM_TOKEN`** — το `install.sh` αντέγραφε μια γνωστή,
  δημοσιευμένη στο repo τιμή· τώρα παράγεται τυχαία per-unit και το
  `selftest.sh` αποτυγχάνει αν βρει το placeholder.
- ✅ **Κοινό admin SSH key σε κάθε shipped unit** — το `prep_image.sh` το
  αφαιρεί πλέον από τα clones (`KEEP_ADMIN_KEY=1` για ρητή διατήρηση), ώστε
  ένα κλεμμένο ιδιωτικό κλειδί να μην ξεκλειδώνει ολόκληρο τον στόλο. Το
  `install.sh` απενεργοποιεί επίσης το password authentication στο SSH —
  **με προστασία**: μόνο αν υπάρχει ήδη authorized key, και με `sshd -t`
  validation πριν το restart, ώστε να μην κλειδωθεί έξω ο ιδιοκτήτης.
