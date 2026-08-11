# IMPROVEMENTS — Τι μπορεί να γίνει καλύτερα ή διαφορετικά

Συμπληρωματικό του `TODO.md`: εκεί καταγράφεται ό,τι **σχεδιάστηκε και δεν
χτίστηκε**· εδώ καταγράφεται ό,τι **υπάρχει και δουλεύει, αλλά θα μπορούσε να
γίνει καλύτερα** — ασφάλεια, ορθότητα, απόδοση, ποιότητα διαδικασίας. Κάθε
εύρημα προέρχεται από πραγματική ανάγνωση του κώδικα (αναφορές `αρχείο:γραμμή`),
όχι από γενικές συμβουλές.

Ετικέτες προσπάθειας: **[εύκολο]** ώρες, **[μέτριο]** 1-2 συνεδρίες,
**[μεγάλο]** ξεχωριστό feature track.

> **⏸️ Κάμερα — παροπλισμένη (2026-08-02).** Όσα ευρήματα παρακάτω αφορούν
> `cam_esp32.ino`, `cam_bridge.py`, `motion.py`, `cam_store.py` ή το
> `camera_screen.dart` (Α5, Β3, Γ1 και σχετικές αναφορές) δεν αφορούν πια
> ζωντανό κώδικα: όλη η κάμερα μετακινήθηκε στο `parked/camera/` και δεν
> εγκαθίσταται ούτε χτίζεται. Τα paths στα ευρήματα είναι προ-παροπλισμού —
> κάθε αρχείο βρίσκεται τώρα κάτω από `parked/camera/` στην ίδια σχετική
> διαδρομή. Δες `parked/camera/README.md`.

---

## Α. Ασφάλεια

### Α1. Secrets μέσα στο git repository **[μέτριο — το πιο σημαντικό εύρημα εδώ]** — δομικά διορθωμένο
**Status:** το σημείο 1 (δομική μετακίνηση) υλοποιήθηκε. Τα σημεία 2-3
παραμένουν χειροκίνητα βήματα για τον χρήστη — βλ. παρακάτω.

Πραγματικά credentials ήταν commited σε tracked αρχεία:
- `firmware/bridge_esp32/bridge_esp32.ino:10-17` — WiFi SSID+password του
  σπιτιού **και** το MQTT password σε plaintext `#define`.
- `firmware/cam_esp32/cam_esp32.ino:18-19` — ίδια WiFi credentials.
- `pi/install.sh:105-112` — πλήρη HiveMQ Cloud credentials (host/user/pass).

Ακόμα κι αν αφαιρεθούν σε επόμενο commit, **μένουν στο git history** — σε
δημόσιο ή κοινοποιημένο repo θεωρούνται διαρρεύσαντα.

1. ✅ **Δομική μετακίνηση (έγινε):** νέα βιβλιοθήκη
   `firmware/libraries/GreenhouseSecrets/` με `secrets.h.example` (tracked,
   placeholder τιμές) — το πραγματικό `secrets.h` είναι gitignored. Τα 4
   sketches (`bridge_esp32`, `cam_esp32`, `edge_node_esp32`,
   `edge_node_esp32_c3`) κάνουν πλέον `#include <secrets.h>` αντί για
   hardcoded `#define`. Το `pi/install.sh` πλέον γράφει `hivemq.json` μόνο
   από το tracked `pi/hivemq.json.example` (placeholder τιμές, μόνο αν δεν
   υπάρχει ήδη) — τα πραγματικά HiveMQ credentials αφαιρέθηκαν εντελώς από
   το repo. `.gitignore` ενημερώθηκε για το νέο `secrets.h`.
2. ⚠️ **Παραμένει ανοιχτό — απαιτεί χειροκίνητη ενέργεια:** οι
   παλιές τιμές (WiFi password, HiveMQ password, MQTT password της γέφυρας)
   **παραμένουν έγκυρες και μένουν στο git history**. Η δομική διόρθωση
   πάνω δεν τις ακυρώνει — απαιτείται **εναλλαγή (rotation)** των ίδιων των
   κωδικών (αλλαγή WiFi password στο router, νέο HiveMQ Cloud password, νέο
   MQTT password για τον χρήστη `app` μέσω `mosquitto_passwd`) από τον
   χρήστη, καθώς αγγίζει πραγματικές, ζωντανές υποδομές (router/HiveMQ
   cloud account) εκτός του πεδίου αυτόματων αλλαγών κώδικα.

### Α2. Το portal τρέχει ως root, χωρίς sandboxing **[εύκολο]** — ✅ έγινε
`pi/systemd/greenhouse-portal.service` είχε `User=root` και **κανένα** από τα
hardening directives που έχουν όλα τα αδελφά services. Τώρα: `User=pi` +
`AmbientCapabilities=CAP_NET_BIND_SERVICE` (για το bind στη θύρα 80) +
`ProtectSystem=strict` + `ProtectHome=read-only` + `ReadWritePaths=` για
`/etc/greenhouse` (sentinel write) και `/run/sudo` (sudo timestamp cache).
`_save_wifi()`/`scan()`/`_reboot_soon()` καλούν πλέον `sudo nmcli`/`sudo
reboot`, εξουσιοδοτημένα μέσω νέου `pi/portal/greenhouse-portal.sudoers`
(εγκαθίσταται από το `install.sh` σε `/etc/sudoers.d/`, validated με
`visudo -c`) — περιορισμένο ακριβώς σε αυτές τις δύο εντολές, όχι blanket
sudo. Σημείωση: `NoNewPrivileges` **δεν** μπήκε σε αυτό το service (σε
αντίθεση με τα αδέλφια του) — θα έκανε το setuid escalation του `sudo` να
αποτυγχάνει σιωπηλά· τεκμηριωμένο trade-off μέσα στο ίδιο το service file.

Το `ExecStartPost=/sbin/iptables ... --dport 8080 -j REDIRECT` (νεκρό
κατάλοιπο — το `ap_up.sh:82` το αφαιρούσε ήδη ως "stale") αφαιρέθηκε.

### Α3. Η γέφυρα μοιράζεται τον MQTT λογαριασμό της εφαρμογής **[εύκολο]** — ✅ έγινε
Η γέφυρα συνδεόταν ως χρήστης `app` (`bridge_esp32.ino:16`) — το
`setup_tls.sh` που δημιουργούσε έναν ξεχωριστό `bridge` λογαριασμό ήταν ήδη
νεκρό κώδικας (διαγράφηκε, βλ. Δ3) και ποτέ δεν καλούνταν στην πράξη. Τώρα:
`pi/scripts/first_boot.sh` παράγει και τα δύο passwords (`app` και `bridge`,
ξεχωριστά, `mosquitto_passwd`), και νέο `pi/mosquitto/acl`
(`acl_file` στο `mosquitto.conf`, μόνο για τον listener 8883) περιορίζει τον
`bridge` σε **publish-only** στα τέσσερα topics που πραγματικά δημοσιεύει
(`greenhouse/+/air/temperature`, `.../air/humidity`, `.../soil/moisture`,
`greenhouse/nodes/+/status` — επιβεβαιωμένο από `bridge_esp32.ino`, ποτέ δεν
κάνει subscribe). `firmware/bridge_esp32/bridge_esp32.ino` παίρνει πλέον
`MQTT_USER`/`MQTT_PASS` από το gitignored `secrets.h` (βλ. finding A1),
πλέον με τιμή `"bridge"` αντί για `"app"`.

### Α4. TLS certificate pinning — τα δεδομένα υπάρχουν ήδη, δεν ελέγχονται ποτέ **[μέτριο]** — ✅ έγινε
Το `/pair` παραδίδει το SHA-256 fingerprint του server certificate και η
εφαρμογή το αποθηκεύει (`ConnectionConfig.tlsFingerprint`) — αλλά το
`onBadCertificate = (Object _) => true` δεχόταν οτιδήποτε. Τώρα:
`mqtt_connection.dart`'s `_matchesPinnedFingerprint()` υπολογίζει SHA-256
του DER του παρουσιαζόμενου certificate και συγκρίνει με το αποθηκευμένο —
μόνο σε ταίριασμα `true`, κενό fingerprint = fail closed (`false`). Κλείνει
το man-in-the-middle κενό που τεκμηριώνεται στο `docs/technical/10-security.md §4`,
χωρίς καμία αλλαγή στο Pi.

### Α7. `POST /cam/frame` εντελώς ανοιχτό — hijack της κάμερας + διαρροή του `CAM_TOKEN` **[κρίσιμο]** — ✅ έγινε (2026-07-28)
Το σοβαρότερο εύρημα ασφαλείας που βρέθηκε σε αυτό το repo. Το
`pi/scripts/cam_bridge.py` άκουγε στο `0.0.0.0:8090` και δεχόταν
`POST /cam/frame` από **οποιονδήποτε**, χωρίς κανέναν έλεγχο. Η αλυσίδα:

1. `_update_heartbeat(request.remote_addr)` όριζε το `_camera_ip` ίσο με τη
   διεύθυνση **του αποστολέα** — ένα POST αρκούσε για να γίνει ο επιτιθέμενος
   «η κάμερα» για το Pi.
2. Κάθε επόμενο fetch του Pi (`_cam_url()` → `/capture`, `/event/<id>`)
   πήγαινε πλέον στον επιτιθέμενο **με `?token=CAM_TOKEN`** — δηλαδή του
   παρέδιδε το ίδιο το token που εξουσιοδοτεί το `DELETE /event/<id>`, άρα τη
   δυνατότητα να **σβήσει τα αποθηκευμένα στιγμιότυπα κίνησης** από την SD
   της πραγματικής κάμερας.
3. Οι εικόνες του επιτιθέμενου προωθούνταν στην εφαρμογή ως live/event frames.
4. Κάθε πλαστό frame μπορούσε να πυροδοτήσει motion event → push notification
   → εγγραφή στη βάση (spam/DoS χωρίς όριο).

**Διόρθωση:** το endpoint απαιτεί πλέον το `CAM_TOKEN` (header `X-Cam-Token`,
ή `?token=` για debugging στον πάγκο), με `hmac.compare_digest`, **fail
closed** αν δεν υπάρχει provisioned token, συν όριο μεγέθους σώματος
(`MAX_FRAME_BYTES`, 413). Το `firmware/cam_esp32/cam_esp32.ino` στέλνει το
header. 7 νέα tests, επαληθευμένα ότι **αποτυγχάνουν** χωρίς τη διόρθωση.

### Α8. `/api/history*` χωρίς καμία αυθεντικοποίηση **[μέτριο]** — ✅ έγινε (2026-07-28)
`GET /api/history` και `/api/history/series` σέρβιραν ολόκληρο το ιστορικό
αισθητήρων της μονάδας σε **οτιδήποτε** έφτανε στη θύρα 80 — κάθε συσκευή στο
LAN ή στο hotspot εγκατάστασης. Ήταν τεκμηριωμένο ως «αποδεκτό, read-only»,
αλλά είναι πλήρης εικόνα δραστηριότητας της εγκατάστασης.

**Διόρθωση:** νέο per-unit `api_token` (32 χαρακτήρες, `first_boot.sh`),
απαιτούμενο ως `Authorization: Bearer` (ή `?token=`), constant-time σύγκριση,
fail closed. Παραδίδεται στην εφαρμογή μέσα στην **ήδη PIN-gated** απάντηση
του `/pair/confirm` — χωρίς νέο βήμα για τον χρήστη. Το `install.sh` κάνει
backfill σε ήδη provisioned units (αλλιώς το `.provisioned` sentinel του
`first_boot.sh` θα τα άφηνε για πάντα χωρίς token, με μόνιμο 401).

### Α9. Timing side-channel στη σύγκριση του PIN **[εύκολο]** — ✅ έγινε (2026-07-28)
`portal.py`'s `pair_confirm()` συνέκρινε `pin != expected_pin` — σύγκριση
string που κάνει short-circuit στον πρώτο διαφορετικό χαρακτήρα, άρα ο χρόνος
απάντησης διέρρεε το σωστό **πρόθεμα**. Με μόνο 10^6 πιθανά PIN, αυτό
μετατρέπει το lockout των 5 προσπαθειών σε επίθεση ψηφίο-ψηφίο. Τώρα
`hmac.compare_digest`, με test που αποτρέπει επαναφορά του `!=` σε μελλοντικό
refactor.

### Α10. Το `bridge` ACL δεν κάλυπτε τα telemetry topics **[εύκολο]** — ✅ έγινε (2026-07-28)
Το `pi/mosquitto/acl` επέτρεπε στον χρήστη `bridge` 4 topics, αλλά το firmware
δημοσιεύει **6** — τα `greenhouse/nodes/+/battery` και `.../mesh` (προστέθηκαν
στο deep-sleep telemetry πέρασμα 2026-07-26) απορρίπτονταν **σιωπηλά** από τον
broker. Ταυτόχρονα bug ασφάλειας-διαμόρφωσης και λειτουργικότητας: το Mesh Map
της εφαρμογής δεν θα έπαιρνε ποτέ δεδομένα στο WiFi-fallback setup, χωρίς
κανένα μήνυμα λάθους πουθενά εκτός από το log του broker.

### Α11. Το pairing lockout ήταν καθολικό — μετατρεπόταν σε DoS **[εύκολο]** — ✅ έγινε (2026-07-28)
Το `_pair_locked` ήταν ένα **καθολικό** flag: 5 λάθος PIN από **οποιαδήποτε**
πηγή κλείδωναν το `/pair/confirm` για όλους, και μόνο restart της υπηρεσίας το
καθάριζε. Δηλαδή ο ίδιος ο μηχανισμός προστασίας ήταν διαθέσιμο όπλο άρνησης
υπηρεσίας: οποιοσδήποτε στο δίκτυο μπορούσε να εμποδίσει μόνιμα τον ιδιοκτήτη
να ζευγαρώσει, και σε μονάδα χωρίς WiFi «restart» σημαίνει φυσικό
power-cycle.

**Διόρθωση:** per-IP καταμέτρηση με αυτόματη λήξη (`PAIR_LOCKOUT_SECONDS`,
15 λεπτά), φραγμένος πίνακας κατάστασης (max 256 εγγραφές, με eviction) ώστε
πηγή που εναλλάσσει διευθύνσεις να μη μεγαλώνει τη μνήμη, και καθολικό
backstop πολύ ψηλότερα (`MAX_GLOBAL_PAIR_FAILURES`) για κατανεμημένες
προσπάθειες. Το `X-Forwarded-For` **δεν** γίνεται δεκτό (δεν υπάρχει proxy
μπροστά από την υπηρεσία) — αλλιώς ο καλών θα έφτιαχνε νέα ταυτότητα ανά
αίτημα και το όριο δεν θα σήμαινε τίποτα. 6 νέα tests.

### Α12. Δημόσιο placeholder ως `CAM_TOKEN` **[εύκολο]** — ✅ έγινε (2026-07-28)
Το `install.sh` αντέγραφε το tracked `cam_token.txt.example`, του οποίου η τιμή
(`your-cam-shared-token`) είναι **δημοσιευμένη στο repo**. Μια μονάδα που δεν
το άλλαζε χειροκίνητα φαινόταν provisioned αλλά το token της δεν
αυθεντικοποιούσε τίποτα. Τώρα παράγεται τυχαία per-unit στο `first_boot.sh`
(άρα και κάθε clone παίρνει δικό του), το `install.sh` **αντικαθιστά** το
placeholder αν το βρει σε υπάρχουσα μονάδα, και το `selftest.sh` αποτυγχάνει
ρητά αν εντοπίσει την παλιά τιμή.

### Α13. Κοινό admin SSH key σε κάθε shipped unit **[μέτριο]** — ✅ έγινε (2026-07-28)
Το `install.sh` έγραφε ένα σταθερό admin public key στο `authorized_keys` κάθε
μονάδας — βολικό για remote support, αλλά σημαίνει ότι **ένα** κλεμμένο
ιδιωτικό κλειδί ξεκλειδώνει κάθε θερμοκήπιο που έχει ποτέ φλασαριστεί, χωρίς
δυνατότητα ανάκλησης χωρίς φυσική επίσκεψη. Τώρα το `prep_image.sh` το
αφαιρεί από τα clones εξ ορισμού (`KEEP_ADMIN_KEY=1` για ρητή διατήρηση σε
στόλο που διαχειρίζεσαι ο ίδιος). Στο ίδιο πέρασμα, το `install.sh`
απενεργοποιεί `PasswordAuthentication`/`PermitRootLogin` — **με προστασία**:
μόνο αν υπάρχει ήδη χρησιμοποιήσιμο authorized key (αλλιώς θα κλείδωνε τον
ιδιοκτήτη έξω), και με `sshd -t` validation + αυτόματο rollback αν το config
δεν περνά.

### Α14. Καμία κρυπτογράφηση μεταφοράς στα ευαίσθητα HTTP endpoints **[μεγάλο]** — ✅ έγινε (2026-07-28)
Το PIN, τα MQTT credentials, το `api_token` και το `cam_token` περνούσαν όλα
από το LAN σε **καθαρό κείμενο** τη στιγμή του ζευγαρώματος. Τα tokens
σταματούσαν τη μη εξουσιοδοτημένη πρόσβαση αλλά όχι έναν παθητικό
παρακολουθητή.

**Διόρθωση:** το portal σερβίρει πλέον **HTTPS στο 8443** με το ίδιο per-unit
πιστοποιητικό που χρησιμοποιεί ο broker (`gen_certs.sh` γράφει
pi-αναγνώσιμο αντίγραφο), και η εφαρμογή κάνει certificate pinning με το ίδιο
fingerprint (νέο κοινό `app/lib/utils/cert_pinning.dart`, το οποίο
χρησιμοποιεί πλέον και το MQTT path ώστε να μην αποκλίνουν). Η θύρα 80
παραμένει plaintext **μόνο** για το captive portal, που δεν λειτουργεί πάνω
από TLS. Προαιρετικός διακόπτης επιβολής (`/etc/greenhouse/require_https`)
κάνει τα plaintext αντίγραφα των ευαίσθητων endpoints να απαντούν 403 —
opt-in, γιατί θα κλείδωνε έξω μη ενημερωμένες εφαρμογές.

**Ρητό εναπομείναν όριο:** first-contact MITM (§8.1 του security doc) — το
fingerprint φτάνει μέσα στην απάντηση ζευγαρώματος, οπότε την πρώτη φορά το
cert γίνεται δεκτό χωρίς επαλήθευση. Παθητική υποκλοπή: καλυμμένη. Ενεργός
MITM τη στιγμή του ζευγαρώματος: όχι· η διαδρομή QR το κλείνει.

### Α5. ESP32-CAM HTTP API εντελώς ανοιχτό στο LAN **[εύκολο]** — ✅ έγινε πλήρως (2026-07-28)
`/stream`, `/capture`, `GET|DELETE /event/<id>` δεν είχαν κανένα auth —
οποιοσδήποτε στο LAN έβλεπε την κάμερα και μπορούσε να **διαγράψει**
αποθηκευμένα γεγονότα κίνησης. Τώρα: νέο `CAM_TOKEN` (network-wide shared
token, ίδιο μοτίβο με το `WIFI_SSID` — όχι per-unit) στο
`firmware/libraries/GreenhouseSecrets/secrets.h`, ελεγμένο σε `/capture` και
`GET|DELETE /event/<id>` (`checkCamToken()` στο `cam_esp32.ino`) — αυτά τα
τρία καλούνται **μόνο** από το `pi/scripts/cam_bridge.py`, που τώρα διαβάζει
την ίδια τιμή από `/etc/greenhouse/cam_token.txt` (χειροκίνητα
provisioned, ίδιο μοτίβο με το `hivemq.json` του Α1) και την επισυνάπτει ως
query param.

**Ολοκληρώθηκε 2026-07-28:** το `/stream` προστατεύεται πλέον κι αυτό. Η
cross-stack αλυσίδα που έλειπε χτίστηκε ολόκληρη: `portal.py`'s
`_pairing_payload()` επιστρέφει `cam_token` (διαβασμένο από το ίδιο
`/etc/greenhouse/cam_token.txt`) → `ConnectionConfig.camToken` →
`camera_screen.dart`'s `streamUrl()` το προσθέτει ως `?token=` →
`cam_esp32.ino`'s `handleStream()` καλεί πλέον `checkCamToken()` όπως τα
υπόλοιπα endpoints. Το token ταξιδεύει μέσα στην **ήδη PIN-gated** απάντηση
του `/pair/confirm`, οπότε δεν προστίθεται βήμα για τον χρήστη· η διαδρομή
χειροκίνητης εισαγωγής το δέχεται από το Advanced section της οθόνης
ζευγαρώματος. Κανένα HTTP endpoint της κάμερας δεν είναι πλέον ανοιχτό.

### Α6. Αχρησιμοποίητος WebSocket listener 9001 **[έγινε]**
Κανένας client δεν τον χρησιμοποιούσε πια (`docs/technical/05-mqtt-broker.md §3`).
Αφαιρέθηκε το listener block από το `pi/mosquitto/mosquitto.conf`. Στο ίδιο
πέρασμα βρέθηκε και δεύτερο, ορφανό σημείο για το ίδιο πράγμα:
`pi/avahi/greenhouse-mqtt.service` διαφήμιζε mDNS για αυτή τη θύρα αλλά
**δεν το εγκαθιστούσε ποτέ το `install.sh`** — διαγράφηκε κι αυτό.

---

## Β. Ορθότητα / Αξιοπιστία

### Β1. Το echo-suppression του HiveMQ bridge καταπίνει νόμιμες επαναλήψεις **[μέτριο]** — ✅ έγινε
Το `_last_seen` cache μπλόκαρε ένα μήνυμα αν το payload του ήταν **ίδιο** με
το τελευταίο που πέρασε από το ίδιο topic, χωρίς όριο χρόνου — μια **γνήσια**
επαναδημοσίευση ίδιας τιμής λεπτά αργότερα επίσης απορριπτόταν για πάντα.
Τώρα: `_last_seen` κρατά `(payload, monotonic_timestamp)` ζεύγη και το
suppression ισχύει μόνο εντός `ECHO_SUPPRESS_WINDOW_S = 2.0` από την
προηγούμενη προώθηση — αρκετό για να κοπεί η πραγματική ηχώ (round-trip σε
ms) χωρίς να μπλοκάρει μια μεταγενέστερη, νόμιμη επανάληψη.

### Β2. Διαρροή μνήμης στο reassembly των live frames της εφαρμογής **[εύκολο]** — ✅ έγινε
`_liveFrameBuffers` κρατούσε buffer ανά `frame_id` επ' αόριστον αν έστω ένα
chunk χανόταν — σε μακρύ lossy live session συσσωρεύονταν ημιτελή frames
στη μνήμη. Τώρα: `_maxInFlightLiveFrames = 2`, με eviction του παλαιότερου
ημιτελούς buffer πριν δημιουργηθεί νέο πέρα από το όριο (ασφαλές γιατί τα
`frame_id` είναι αύξοντα και ο `Map` insertion-ordered). Καλυμμένο με νέο
test στο `greenhouse_repository_test.dart`.

### Β3. Το LAN live streaming παγώνει την ανίχνευση κίνησης **[μέτριο]**
Ο `WebServer` του ESP32 είναι single-threaded και το `handleStream()`
(`cam_esp32.ino:86-104`) τρέχει `while (client.connected())` — όσο κάποιος
βλέπει το MJPEG stream, το `loop()` δεν εκτελείται ποτέ, άρα το
`sendSnapshotToPi()` σταματά ⇒ **καμία ανίχνευση κίνησης και κανένα
heartbeat** όσο διαρκεί η ζωντανή προβολή (το Pi μάλιστα θα δει την κάμερα
"offline" μετά από 9s streaming — `HEARTBEAT_STALE_SECONDS`). Λύσεις: (α)
μεταφορά σε `ESPAsyncWebServer`, ή (β) φραγή διάρκειας streaming με περιοδικό
yield που στέλνει snapshot ενδιάμεσα. Τουλάχιστον να τεκμηριωθεί ως γνωστή
συμπεριφορά αν μείνει ως έχει.

### Β4. Καμία ρητή επανασύνδεση WiFi στη γέφυρα μετά το boot **[εύκολο]** — ✅ έγινε
Το `bridge_esp32.ino` έκανε blocking WiFi connect μόνο στο `setup()`, χωρίς
κανέναν έλεγχο στο `loop()`. Τώρα: νέα `checkWifi()`, καλείται σε κάθε
`loop()` iteration, με το ίδιο "χαμηλού κόστους έλεγχος κάθε λίγα
δευτερόλεπτα" πνεύμα με το ήδη υπάρχον non-blocking MQTT reconnect. Ελέγχει
`WiFi.status()` κάθε `WIFI_CHECK_INTERVAL_MS` (5s)· αν αποσυνδεδεμένο,
ξαναδοκιμάζει `WiFi.reconnect()` σε κάθε έλεγχο· αν παραμείνει
αποσυνδεδεμένο για `WIFI_RECONNECT_TIMEOUT_MS` (30s), κάνει `ESP.restart()`
ως backstop (καλύπτει την περίπτωση όπου το `reconnect()` της ίδιας της
στοίβας έχει κολλήσει).

### Β5. Η σάρωση καναλιού των edge nodes δένει με το SSID του router **[μέτριο]** — ✅ λύθηκε (διαφορετικά από το προτεινόμενο)
Κάθε edge node έβρισκε το ESP-NOW κανάλι σαρώνοντας για το hardcoded
`WIFI_SSID` του σπιτιού. Αν ο χρήστης μετονόμαζε το router του, **όλοι οι
κόμβοι ήθελαν reflash** — παρόλο που οι ίδιοι δεν συνδέονταν ποτέ στο WiFi.
Το προτεινόμενο fix παρακάτω (σάρωση για το beacon της γέφυρας αντί για το
SSID) δεν χτίστηκε ποτέ — αντ' αυτού, το UART-wired bridge πέρασμα
(2026-07-27, `docs/superpowers/specs/2026-07-20-uart-bridge-design.md`)
έκανε ολόκληρο το πρόβλημα άσχετο: κάθε κόμβος (γέφυρα + και τα δύο edge
sketches) κλειδώνει πλέον σε σταθερό `MESH_FIXED_CHANNEL`
(`mesh_config.h`), χωρίς καμία σάρωση — επιβεβαιωμένο (`grep` και στα δύο
edge sketches, καμία αναφορά `WIFI_SSID` πια). Ήταν παράπλευρη συνέπεια της
αφαίρεσης της εξάρτησης από router για το UART deployment mode, όχι
σκόπιμη διόρθωση αυτού του συγκεκριμένου ευρήματος, αλλά το κλείνει: η
μετονομασία του router (ή η απουσία router εντελώς) δεν απαιτεί πια
reflash κανενός κόμβου.

Αρχική πρόταση (δεν υλοποιήθηκε, ξεπεράστηκε): σάρωση των 13 καναλιών
ακούγοντας για το ίδιο το beacon της γέφυρας (rank 0, `MESH_MAGIC`) αντί
για το SSID.

### Β6. Ψευδές "offline" σε σειρά αποτυχιών DHT **[εύκολο]** — ✅ έγινε
Η ζωντάνια κόμβου βασιζόταν μόνο σε άφιξη **δεδομένων**, και οι δύο edge
sketches παρέλειπαν εντελώς το `meshSendReading()` όταν ο DHT απέτυχε
(`isnan`) — οπότε ένας κόμβος που ζει και κάνει beacon κανονικά αλλά έχει
διαδοχικά NaN δηλωνόταν ψευδώς offline. Τώρα και τα δύο edge sketches
καλούν πάντα `meshSendReading()`, ακόμα και με NaN temperature/humidity (το
NaN περνάει σωστά μέσα από ESP-NOW — ίδια IEEE-754 κωδικοποίηση και στις
δύο άκρες)· η γέφυρα (`onDataRecv`) ανανεώνει το `lastSeenMs`/`nodeOnline`
όπως πάντα, αλλά πλέον παραλείπει να δημοσιεύσει μόνο το/τα NaN metric(s)
αντί να στείλει το literal string `"nan"` σε topic που η εφαρμογή το
περιμένει ως float — το soil moisture (δεν έρχεται από DHT) δημοσιεύεται
κανονικά ό,τι κι αν συμβεί με τον DHT.

### Β7. Σιωπηλά `catch (_) {}` στο repository της εφαρμογής **[εύκολο]** — ✅ έγινε
Πολλαπλά σημεία στο `greenhouse_repository.dart` κατάπιναν parse errors
ολοκληρωτικά — ένα κακοσχηματισμένο payload θα εξαφανιζόταν χωρίς ίχνος.
Όλα τα bare `catch (_) {}` της `_handle()`/live-frame-chunk λογικής έγιναν
`catch (e) { if (kDebugMode) debugPrint('...: $e'); }`, με ξεχωριστό μήνυμα
ανά σημείο για να λέει τι ακριβώς απέτυχε να γίνει parse.

### Β8. Mesh map: "last seen" έδειχνε τρέχουσα ώρα σε offline κόμβους **[εύκολο]** — ✅ έγινε (2026-08-11)
Το `greenhouse_repository.dart` έγραφε `lastSeen: event.lastSeen` σε *κάθε*
`/status` merge, και το `event.lastSeen` είναι πάντα `DateTime.now()` τη
στιγμή του parse — ό,τι κι αν έλεγε το payload. Ένα offline μήνυμα δεν
κουβαλάει δικό του timestamp (η γέφυρα/serial_bridge στέλνουν απλά το
string `"offline"` τη στιγμή που το εντοπίζουν), οπότε ένας κόμβος που
μόλις χάθηκε έδειχνε «είδα τον μόλις τώρα» — ακριβώς το αντίθετο. Τώρα το
lastSeen προχωράει μόνο σε μετάβαση σε **online**· σε offline κρατά την
προηγούμενη τιμή. `NodeListTile`/το detail sheet του mesh map δείχνουν
πλέον και ημερομηνία όταν το lastSeen δεν είναι σημερινό
(`app/lib/utils/last_seen_format.dart`), αλλιώς ένα παλιό `HH:mm` διαβάζεται
σαν πρόσφατο.

**Γνωστό υπολειπόμενο κενό, τεκμηριωμένο σε σχόλιο όχι διορθωμένο:** ένα
*retained* MQTT μήνυμα (π.χ. σε reconnect της εφαρμογής) ξαναπροχωράει το
lastSeen σε "τώρα" ακόμα κι αν είναι ώρες παλιό — το πρωτόκολλο δεν
κουβαλάει καθόλου πραγματικό timestamp ώστε να ξεχωρίσει live delivery από
retained replay. Η σωστή διόρθωση θέλει να περάσει το MQTT `retain` flag
μέσα από `mqtt_connection.dart` ως τα models — μεγαλύτερη αλλαγή,
σκόπιμα εκτός εμβέλειας αυτού του περάσματος.

### Β9. Mesh map: καμία ένδειξη "direct στη γέφυρα" vs "relayed" **[εύκολο]** — ✅ έγινε (2026-08-11)
Το `meshRank` ΕΙΝΑΙ ήδη αριθμός αλμάτων από τη γέφυρα σε αυτό το mesh (μία
γέφυρα, rank=hops), αλλά τίποτα στην οθόνη δεν το μετέφραζε σε κάτι
αναγνώσιμο — έπρεπε να ξέρεις τη σύμβαση "rank 1 = απευθείας" εκ των
προτέρων. Προστέθηκε ρητή ετικέτα "Direct" / "N hops" πάνω στην κάρτα
(`mesh_node_card.dart`) και αναλυτική γραμμή "Connection" στο detail sheet
("Direct to bridge" / "Relayed — 2 hops (via node1)" / "This is the
bridge" / "Unknown — no mesh data yet"). Παράλληλα προστέθηκε legend για τα
χρώματα σύνδεσης και μια γραμμή σύνοψης (N nodes · N online · N offline) —
έλειπε εντελώς οποιαδήποτε εξήγηση τι σημαίνουν τα χρώματα των γραμμών.

### Β10. Η εφαρμογή πυροδοτούσε ψευδή security alert στο history **[εύκολο]** — ✅ έγινε (2026-08-11)
Ένα κινητό ζευγαρωμένο πριν τις 2026-07-28 (πριν προστεθεί το `api_token`
στο `/pair/confirm`) έχει άδειο `apiToken` αποθηκευμένο τοπικά. Κάθε άνοιγμα
του weather/zone history έστελνε αίτημα στο `/api/history` χωρίς κανένα
token, το `portal.py` σωστά απαντούσε 401, και επειδή το
`history_auth_failure` είναι alertable kind στο `security_log.py`, το Pi
έστελνε "Greenhouse security alert" push στο ίδιο το κινητό του ιδιοκτήτη —
φαινόταν σαν επίθεση, ήταν απλά ξεπερασμένο pairing. Η εφαρμογή τώρα
ελέγχει `config.apiToken.isEmpty` **πριν** στείλει το αίτημα
(`historyPointsProvider`, νέο `HistoryTokenMissingException`) αντί να
ρίξει ένα σίγουρα-θα-αποτύχει request στο δίκτυο, και το history screen
δείχνει κουμπί **Re-pair** αντί για σκέτο "401". Ο ιδιοκτήτης χρειάζεται να
ξαναζευγαρώσει μία φορά για να πάρει πραγματικό token.

---

## Γ. Απόδοση

### Γ1. Motion diff σε καθαρή Python στο Pi Zero W **[εύκολο]** — ✅ έγινε
`motion.diff_score()` έκανε `sum(abs(p - c) for p, c in zip(...))` πάνω σε
4.800 pixels σε ερμηνευμένη Python, κάθε 3 δευτερόλεπτα, σε single-core
ARMv6 1GHz. Τώρα χρησιμοποιεί `Image.frombytes` + `ImageChops.difference` +
`ImageStat.Stat().mean[0]` (PIL, ήδη dependency) — ίδιο μαθηματικό
αποτέλεσμα, υπολογισμένο σε C. Τα υπάρχοντα tests (`test_motion.py`)
επιβεβαιώνουν ισοδυναμία.

### Γ2. Fallback σειρά σύνδεσης όταν είσαι εκτός σπιτιού **[εύκολο]** — ✅ έγινε
`_attempt()` δοκίμαζε **πάντα** πρώτα το LAN host με 5s timeout πριν το
remote — κάθε (επανα)σύνδεση εκτός σπιτιού πλήρωνε 5 χαμένα δευτερόλεπτα σε
host που δεν θα απαντούσε ποτέ. Τώρα: `SharedPreferences` θυμάται αν η
τελευταία επιτυχής σύνδεση ήταν `'local'` ή `'remote'` και δοκιμάζει αυτό
πρώτα — συμμετρικό κόστος λάθους, κέρδος 5s στο κοινό away-from-home
σενάριο.

### Γ3. Subprocess-based MQTT στο weather.py **[μέτριο — μόνο αν χρειαστεί]**
Κάθε κύκλος (κάθε 30 λεπτά σε production, κάθε 30s με το τρέχον debug
interval) εκτελεί ~3 `mosquitto_sub` + N `mosquitto_pub` subprocesses. Είναι
συνειδητή επιλογή απλότητας που δουλεύει λόγω retained topics
(`docs/technical/05-mqtt-broker.md §7`) — δεν χρειάζεται αλλαγή σήμερα.
Καταγράφεται ώστε αν το service αποκτήσει συχνότερους κύκλους ή
περισσότερα topics, το πέρασμα σε persistent paho client (όπως
recorder/cam_bridge) να είναι η γνωστή επόμενη κίνηση.

---

## Δ. Διαδικασία / Ποιότητα repo

### Δ1. Καθόλου CI **[έγινε — `.github/workflows/ci.yml`]**
Δεν υπήρχε `.github/workflows/` — κάθε PR αυτής της περιόδου περνούσε με
μηδέν checks. Υπήρχαν ήδη **120 Python tests** (`pi/tests/`, pytest) και
**~104 Dart tests** (`app/test/`) + `flutter analyze` που έτρεχαν μόνο
χειροκίνητα. Προστέθηκε workflow δύο jobs (pytest· `flutter analyze &&
flutter test`) — επαληθεύτηκε τοπικά πριν το push (`pytest pi/tests/` σε
καθαρό venv). Τα firmware sketches παραμένουν εκτός CI (δεν υπάρχει
toolchain χωρίς φυσικό hardware) — εκτός scope, ίδιο μοτίβο με τα mesh/
camera bench-tests.

**Πραγματικό bug που βρέθηκε φτιάχνοντας το CI:** 2 από τα 120 tests
(`test_push.py`) απέτυχαν όταν το `firebase-admin` λείπει, γιατί το
`pi/shared/push.py:13-18` ορίζει το `messaging` name **μέσα** στο
`try/except ImportError` — αν η βιβλιοθήκη λείπει, το όνομα δεν υπάρχει
καθόλου στο module namespace, άρα το `monkeypatch.setattr(push,
'messaging', ...)` σκάει με `AttributeError` αντί να τρέξει το
mock-based σενάριο του test. Λύθηκε στο επίπεδο του workflow
(εγκατάσταση `firebase-admin` στο CI, ίδιο με το πραγματικό Pi μέσω
`install.sh`) αντί να αλλάξει το `push.py` — μικρότερο, πιο ασφαλές diff.

### Δ2. debugPrint / logging τακτοποίηση **[εύκολο]** — μερικώς έγινε
Η ουσία (θόρυβος σε release builds) είναι ήδη λυμένη: και τα 7 `debugPrint`
σε `mqtt_connection.dart`/`connection_provider.dart`/
`greenhouse_repository.dart`/`notification_service.dart` είναι πλέον
τυλιγμένα σε `if (kDebugMode) debugPrint(...)` (βλ. Β7 — έγινε στο ίδιο
πέρασμα). Ό,τι απομένει είναι καθαρά καλαισθητικό: ένα ενιαίο μικρό log
helper αντί για 7 επαναλαμβανόμενα `if (kDebugMode)` guards — δεν αλλάζει
συμπεριφορά, χαμηλή προτεραιότητα.

### Δ3. Νεκρά/παραπλανητικά αρχεία **[εύκολο]** — ✅ έγινε
- `pi/mosquitto/setup_tls.sh` — απαιτούσε «tailscale-ip» όρισμα, από την
  εγκαταλελειμμένη Tailscale εποχή· είχε αντικατασταθεί πλήρως από
  `gen_certs.sh` + `install.sh`. **Διαγράφηκε** — δεν υπάρχει πια στο repo.
- Η `ExecStartPost` iptables γραμμή του portal.service (βλ. Α2) — αφαιρέθηκε.
- Checkbox state στα plan αρχεία: παραμένει ανοιχτό ως σημείωση διαδικασίας
  (χαμηλή προτεραιότητα) — τα plan αρχεία εξακολουθούν να έχουν 0/N
  τσεκαρισμένα κουτιά ανεξαρτήτως πραγματικής κατάστασης· `TODO.md` παραμένει
  η αξιόπιστη πηγή αντ' αυτού.
- `HANDOFF.md` — το "Full backlog" section είχε ξεπεράσει σε staleness το
  ίδιο το `TODO.md` που δημιουργήθηκε για να το αντικαταστήσει (π.χ.
  ισχυριζόταν ότι το `/pair` ήταν ανεπιβεβαίωτο αφού το PIN-auth είχε ήδη
  χτιστεί) — αντικαταστάθηκε με παραπομπή στο `TODO.md`/`IMPROVEMENTS.md`
  ως ενιαία πηγή αλήθειας (βλ. session αυτού του περάσματος στο `HANDOFF.md`).

### Δ4. Ο simulator ως προαιρετικό service **[εύκολο]** — ✅ έγινε
Νέο `pi/systemd/greenhouse-simulator.service` (installed by `install.sh`,
disabled by default — `systemctl enable --now greenhouse-simulator` μόνο σε
demo/no-real-sensor units) αντικαθιστά το παλιό transient `systemd-run`
snippet. Η κατάσταση είναι πλέον ρητή (ένα κανονικό service αρχείο) αντί
για προφορική γνώση σε ένα README block.

### Δ5. Topic για αισθητήρα φωτός χωρίς αισθητήρα **[σημείωση, όχι δράση]**
Ο recorder κάνει subscribe στο `greenhouse/+/light/lux` και ο simulator το
δημοσιεύει, αλλά κανένα πραγματικό firmware δεν στέλνει φωτεινότητα (το
`SensorPacket` έχει μόνο temp/humidity/soil). Είτε είναι σκόπιμη
προετοιμασία για μελλοντικό αισθητήρα (οπότε ΟΚ ως έχει), είτε αξίζει μια
γραμμή στο `TODO.md` ως σχεδιαζόμενο hardware — να αποφασιστεί συνειδητά.

---

## Προτεινόμενη σειρά — ενημερωμένο, μόνο ό,τι απομένει πραγματικά ανοιχτό

Η πλειονότητα αυτής της λίστας είναι πλέον ✅ done: Δ1 CI, Α2, Α4, Α5, Α6,
**Α7-Α10** (πέρασμα σκλήρυνσης 2026-07-28), Β1, Β2, Β4, Β5, Β6, Β7, Γ1, Γ2,
Δ3, Δ4 — βλ. τα ξεχωριστά ευρήματα παραπάνω. Ό,τι πραγματικά απομένει, με
σειρά προτεραιότητας:

1. **Α1, σημεία 2-3 (rotation)** — **το μόνο ανοιχτό θέμα ασφάλειας που δεν
   μπορεί να λυθεί με κώδικα**: τα παλιά credentials παραμένουν έγκυρα και
   μένουν στο git history. Απαιτεί χειροκίνητη αλλαγή σε πραγματικές,
   ζωντανές υποδομές (router, HiveMQ account, `mosquitto_passwd`).
2. **Ενεργοποίηση του `require_https`** — μόλις κάθε ζευγαρωμένο τηλέφωνο
   τρέχει build που μιλάει HTTPS: `sudo touch /etc/greenhouse/require_https`
   + restart του portal. Κλείνει τη διαδρομή υποβάθμισης σε plaintext. Δεν
   είναι ενεργό εξ ορισμού γιατί θα κλείδωνε έξω μη ενημερωμένες εφαρμογές.
3. ~~**TLS στη ζεύξη της κάμερας**~~ — **PARKED μαζί με την κάμερα (2026-08-02)**,
   δες το banner στην κορυφή αυτού του αρχείου. Αν η κάμερα ξαναχτιστεί, αυτό
   παραμένει η μόνη plaintext ζεύξη· βλ. `10-security.md §8.2`.
4. ~~**Β3 (LAN streaming μπλοκάρει motion detection)**~~ — **PARKED μαζί με
   την κάμερα**, ίδιο σημείωμα.
5. **Δ2 (log helper)** — καθαρά καλαισθητικό, χαμηλή προτεραιότητα. Το μόνο
   πραγματικά ανοιχτό στοιχείο εκτός Α1/`require_https` πλέον.
