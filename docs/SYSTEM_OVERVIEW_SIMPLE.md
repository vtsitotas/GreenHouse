# Το σύστημα με απλά λόγια — layout + ασφάλεια

Αυτό το έγγραφο είναι ο απλός, «καθισμένοι δίπλα και σου εξηγώ» οδηγός του
project: πώς κουμπώνει το ένα κομμάτι στο άλλο, και τι έχει γίνει για
ασφάλεια και γιατί. Δεν αντικαθιστά το `docs/ARCHITECTURE.md` (διαγράμματα)
ή το `docs/technical/10-security.md` (πλήρης τεχνική ανάλυση) — είναι το
πρώτο πράγμα να διαβάσεις πριν από εκείνα.

**Τελευταία ενημέρωση:** 2026-08-11.

---

## Μέρος Α — Πώς είναι στημένο όλο το σύστημα

### Το Pi είναι ο εγκέφαλος, αλλά είναι μόνο ένα Pi Zero W

Κανένα cloud IoT platform, καμία InfluxDB/Grafana/Node-RED. Ένα Pi Zero W
(ένας πυρήνας, 512MB RAM) τρέχει τα πάντα μόνο του, ως έξι μικρά systemd
services (`pi/systemd/`):

- `greenhouse-serial-bridge` — διαβάζει το καλώδιο από τη γέφυρα
- `greenhouse-recorder` — γράφει στη βάση
- `greenhouse-weather` — καιρός + κανόνες αυτοματισμού
- `greenhouse-portal` — το web κομμάτι (setup + pairing + history API)
- `greenhouse-hivemq-bridge` — απομακρυσμένη πρόσβαση
- (`greenhouse-ap`, `greenhouse-wifi-watchdog`, `greenhouse-firstboot` —
  βοηθητικά της πρώτης εγκατάστασης)

Στη μέση όλων κάθεται ο **Mosquitto** (MQTT broker) — σαν ταχυδρομείο μέσα
στο μηχάνημα. Κανένα service δεν μιλάει απευθείας σε άλλο· όλα δημοσιεύουν
σε "topics" και όποιος ενδιαφέρεται κάνει subscribe.

### Από τον αισθητήρα μέχρι το Pi

**Κόμβος αισθητήρα** (ESP32-C3, μπαταρία) → μιλάει **ESP-NOW** (layer 2 της
Espressif, πάνω σε 802.11 action frames — καμία IP, καμία θύρα TCP/UDP).
Γιατί όχι WiFi: σηκώνοντας WiFi stack αδειάζεις μπαταρία· με ESP-NOW
ξυπνάς, στέλνεις, κοιμάσαι, σε χιλιοστά δευτερολέπτου.

Οι κόμβοι φτιάχνουν **mesh μόνοι τους**: εκπέμπουν beacons με
`{MAC, rank, seq}` — η γέφυρα είναι rank 0, όποιος την ακούει απευθείας
γίνεται rank 1, όποιος ακούει μόνο έναν rank 1 γίνεται rank 2 και στέλνει
μέσω αυτού. Αν πέσει ενδιάμεσος κόμβος, οι υπόλοιποι ξαναδιαλέγουν γονέα
μόνοι τους. Τα δεδομένα μέτρησης ταξιδεύουν κρυπτογραφημένα (AES-128-CTR)·
τα beacons είναι αναγκαστικά plaintext (hardware περιορισμός σε broadcast
frames του ESP-NOW), αλλά αποκαλύπτουν μόνο rank/MAC, ποτέ μέτρηση. Κόμβοι
μπαταρίας κάνουν και **deep sleep** — κρατούν την κατάσταση mesh σε RTC
memory (επιβιώνει τον ύπνο) και κάνουν self-heal αν ξυπνήσουν και ο γονέας
δεν απαντά.

**Η γέφυρα** (ESP32-C3, rank 0) είναι πλέον **βιδωμένη πάνω στο Pi με τρία
σύρματα** (TX/RX/GND, 115200 baud, 3.3V και στις δύο πλευρές — καμία WiFi,
κανένα MQTT, κανένα credential πάνω της). Γράφει JSON γραμμές στο σειριακό·
το `pi/scripts/serial_bridge.py` τις διαβάζει και είναι ο μοναδικός MQTT
client που κάνει publish για λογαριασμό των αισθητήρων, σε topics όπως
`greenhouse/zone1/air/temperature`, `greenhouse/nodes/<mac>/status`,
`/battery`, `/mesh` — όλα με `retain=true` ώστε μια εφαρμογή που μόλις
άνοιξε να βλέπει αμέσως τελευταία τιμή.

### Μέσα στο Pi

Ο Mosquitto ακούει σε δύο θύρες:
- **1883** — μόνο `127.0.0.1`, χωρίς κρυπτογράφηση/auth. Ασφαλές επειδή ο
  ίδιος ο πυρήνας αρνείται εξωτερική σύνδεση, όχι επειδή το εμπιστευόμαστε.
- **8883** — TLS, username/password, ACL. Εδώ συνδέεται η εφαρμογή.

**Ο recorder** δεν γράφει κάθε μέτρηση στον δίσκο (θα έτρωγε την SD) —
μαζεύει στη RAM ανά λεπτό (avg/min/max) και γράφει μαζικά κάθε 60″. SQLite σε
WAL mode: `readings` (ανά λεπτό, 90 μέρες) → συμπυκνώνεται σε
`readings_hourly` (ανά ώρα, 2 χρόνια).

**Ο weather service** τραβάει πρόγνωση από Open-Meteo και τρέχει τη μηχανή
κανόνων — όταν ενεργοποιείται κανόνας, δημοσιεύει
`greenhouse/actuators/<x>/set`. Οι κανόνες/ρυθμίσεις έρχονται από την
εφαρμογή ως retained MQTT μηνύματα.

### Το portal: δύο ρόλοι σε ένα Flask app

**Φάση 1 — captive portal.** Παρθένο Pi → δικό του ανοιχτό hotspot
`Greenhouse-XXXX` → φόρμα WiFi πάνω από plaintext HTTP :80 (σκόπιμα plaintext,
αλλιώς δεν πυροδοτείται το captive-portal popup του κινητού).

**Φάση 2 — κανονική λειτουργία.** Το ίδιο service σερβίρει `/pair`,
`/pair/confirm`, `/api/history*` — **και στη θύρα 80 και στη 8443 με TLS**.

### Πώς βρίσκει το κινητό το θερμοκήπιο

Avahi διαφημίζει `greenhouse.local` (mDNS). Μετά: `GET /pair` (μόνο
`{"found":true}`, καμία πληροφορία) → σκανάρεις **QR** ή βάζεις 6ψήφιο PIN →
`POST /pair/confirm` επιστρέφει credentials + API token + TLS fingerprint →
η εφαρμογή συνδέεται στο 8883 με **certificate pinning** πάνω σε αυτό το
fingerprint. Το QR είναι ο ασφαλής δρόμος, γιατί μεταφέρει το fingerprint
**εκτός δικτύου**.

Δύο MQTT λογαριασμοί: `app` (η εφαρμογή, `readwrite greenhouse/#`) και
`bridge` (η γέφυρα, **publish-only** στα δικά της topics — μια παραβιασμένη
γέφυρα δεν μπορεί να στείλει εντολή σε actuator ούτε να διαβάσει τίποτα).

### Εκτός σπιτιού

Το `hivemq_bridge.py` κρατά σύνδεση με **HiveMQ Cloud** και γεφυρώνει
topics και προς τις δύο κατευθύνσεις — το **μοναδικό** σημείο στο σύστημα
με πλήρη TLS επικύρωση (δημόσιο CA store, γιατί περνά από το ανοιχτό
Internet). Τα γραφήματα ιστορικού εκτός LAN δεν πάνε μέσω HTTP (το HiveMQ
γεφυρώνει μόνο MQTT) — η εφαρμογή στέλνει το ερώτημα ως MQTT μήνυμα
(`greenhouse/history/request` → `.../response/<id>`), απαντά ο recorder.

### Η εφαρμογή

Flutter/Riverpod. Dashboard ανά ζώνη, Control (actuators), Devices (λίστα
κόμβων + **live mesh map** με drag-to-pin), Weather+Rules, History,
Settings, Pairing.

### Τι είναι παροπλισμένο

Η **ESP32-CAM** χτίστηκε πλήρως (live stream, motion events, HMAC signing,
AES-GCM) αλλά δεν πρόλαβε να τρέξει σε πραγματικό υλικό — μεταφέρθηκε στο
`parked/camera/` στις 2026-08-02, δεν εγκαθίσταται σήμερα.

---

## Μέρος Β — Τι αλλάξαμε για ασφάλεια, και γιατί

### 1. Πέντε πραγματικές τρύπες που έκλεισαν (πέρασμα 2026-07-28)

- **`POST /cam/frame` ήταν τελείως ανοιχτό.** Ένα POST από οποιονδήποτε στο
  LAN σε έκανε «την κάμερα» για το Pi· το Pi μετά έστελνε το `CAM_TOKEN`
  στον ίδιο τον επιτιθέμενο — άρα και τη δυνατότητα να σβήσει αποθηκευμένα
  motion events. Η σοβαρότερη τρύπα του περάσματος.
- **`/api/history*`** σέρβιρε ολόκληρο το ιστορικό σε οποιονδήποτε στη
  θύρα 80. Τώρα θέλει `Authorization: Bearer <api_token>`.
- **`/stream` της κάμερας** ήταν ανοιχτό σε κάθε συσκευή δικτύου.
- **Το PIN συγκρινόταν με `!=`** — timing attack, το σωστό πρόθεμα
  διέρρεε μέσω χρόνου απόκρισης. Τώρα constant-time.
- **Το Mosquitto ACL** κάλυπτε 4 από τα 6 topics της γέφυρας· `/battery`
  και `/mesh` απορρίπτονταν σιωπηλά.

### 2. Κρυπτογράφηση μεταφοράς

Το portal σερβίρει **HTTPS στο 8443** με το per-unit πιστοποιητικό· η
εφαρμογή κάνει **certificate pinning** με το ίδιο fingerprint που ήδη
καρφιτσώνει για MQTT. Η θύρα 80 μένει plaintext **μόνο** για το captive
portal. Τα καρέ κάμερας ήταν HMAC-υπογεγραμμένα + AES-256-GCM
κρυπτογραφημένα (μέρος του παροπλισμένου κώδικα σήμερα).

### 3. Μυστικά: μοναδικά ανά μονάδα, εναλλάξιμα

`api_token`/`cam_token` παράγονται στο πρώτο boot ανά συσκευή (πριν, το
`cam_token` ήταν δημόσιο placeholder του repo). `rotate_secrets.sh`
αλλάζει με μία εντολή κάθε μυστικό που ανήκει στο Pi. Το
`check_leaked_secrets.py` **αποτυγχάνει το healthcheck** όσο κάποιο
credential που έχει διαρρεύσει στο git history είναι ακόμα ζωντανό —
κρατά μόνο SHA-256 hashes, ποτέ τις τιμές.

### 4. Άρνηση υπηρεσίας (DoS)

Το pairing lockout ήταν καθολικό και λατσαρισμένο μέχρι restart —
οποιοσδήποτε στο δίκτυο μπορούσε να μπλοκάρει **μόνιμα** τον ιδιοκτήτη με
5 λάθος PIN. Τώρα per-IP, με αυτόματη λήξη (15′) και καθολικό backstop.

### 5. Ανίχνευση επιθέσεων

`pi/shared/security_log.py`: δομημένο audit log
(`/var/log/greenhouse-security.log`) + push ειδοποίηση στο κινητό για τα
σοβαρά events, **rate-limited σε 1 ανά είδος ανά 15′** — χωρίς αυτό το
όριο, το κανάλι ειδοποιήσεων γίνεται όπλο (κάποιος σε πλημμυρίζει μέχρι να
κάνεις mute, και μετά χάνεις την πραγματική ειδοποίηση).

### 6. Σκλήρυνση συστήματος

systemd sandbox σε όλα τα services (μηδενικό `CapabilityBoundingSet`,
`ProtectSystem=strict`, κλπ.)· SSH password auth off (με προστασία — μόνο
αν υπάρχει ήδη authorized key)· το κοινό admin SSH key δεν μπαίνει πια σε
νέα clones· προαιρετικό firewall (`firewall.sh`, default-deny, όχι
αυτόματο).

### 7. Το git history καθαρίστηκε (2026-08-11, σήμερα)

Το commit `c0383b3` (παλιό, Ιούλιος) κουβαλούσε **πραγματικό WiFi
password και MQTT password** σε plaintext, μέσα στο `bridge_esp32.ino`.
Το να τα μετακινήσεις σε gitignored `secrets.h` αργότερα δεν τα ακύρωνε —
όποιος είχε clone το repo τα κρατούσε ακόμα.

Σήμερα έτρεξε `git filter-repo --replace-text` σε **όλα τα 223 commits**·
αφαιρέθηκαν 2 WiFi passwords (router + hotspot κινητού), 2 SSID, το MQTT
password, και ένα πρόχειρο `"123"` MQTT password. Επαληθεύτηκε με fresh
clone από το GitHub: **μηδέν εμφανίσεις** των τιμών σε κανένα commit.
Force-pushed.

**Αυτό δεν είναι rotation.** Όποιος έκανε clone πριν τις 2026-08-11 κρατά
ακόμα λειτουργικά credentials — το scrub κρύβει τις τιμές από νέους
αναγνώστες, δεν τις ακυρώνει. Το `rotate_secrets.sh` (αυτόματο στο Pi) και
η χειροκίνητη αλλαγή router/HiveMQ password παραμένουν ανοιχτά βήματα.

### 8. Δύο ακόμα διορθώσεις σήμερα (όχι στο αρχικό πέρασμα, αλλά ίδιας λογικής)

**Ψευδές security alert στο history.** Ένα paired κινητό από πριν τις
2026-07-28 δεν είχε ποτέ πάρει `api_token` (δεν υπήρχε ακόμα το πεδίο) —
οπότε κάθε άνοιγμα του weather history έστελνε αίτημα χωρίς token, το Pi
σωστά απαντούσε 401, και επειδή το `history_auth_failure` είναι alertable
event, έστελνε "security alert" στο ίδιο το κινητό του ιδιοκτήτη. Δεν ήταν
επίθεση — ήταν ξεπερασμένο pairing. Η εφαρμογή τώρα **δεν στέλνει καν το
αίτημα** όταν ξέρει ότι δεν έχει token, και δείχνει κουμπί «Re-pair» αντί
για το σκέτο 401.

**Λάθος «last seen» σε offline κόμβους (mesh map).** Ο κώδικας έγραφε
`lastSeen: DateTime.now()` ακόμα και σε offline events — ένας κόμβος που
μόλις χάθηκε έδειχνε «είδα τον τώρα». Τώρα το lastSeen προχωράει μόνο σε
μετάβαση σε online.

---

## Τι μένει ανοιχτό — να το ξέρεις

1. **Rotation μετά το scrub** — router/HiveMQ password χειροκίνητα.
2. **Active MITM στο πρώτο mDNS pairing** — μόνο ανιχνεύεται (safety
   code), δεν αποτρέπεται. Με QR ή χειροκίνητο fingerprint, αποτρέπεται.
3. **Φυσική κλοπή mesh κόμβου** εκθέτει το κοινό-δικτύου ESP-NOW PMK/LMK.
4. **Φυσική πρόσβαση στο Pi** — η SD δεν είναι κρυπτογραφημένη.
5. **`sudo nmcli` χωρίς password** για τον `pi` — απαραίτητο για το WiFi
   setup, αλλά ευρύ αν παραβιαστεί οποιαδήποτε υπηρεσία.

Πλήρης λίστα και σκεπτικό: `SECURITY.md` και `docs/technical/10-security.md`.
