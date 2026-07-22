# IMPROVEMENTS — Τι μπορεί να γίνει καλύτερα ή διαφορετικά

Συμπληρωματικό του `TODO.md`: εκεί καταγράφεται ό,τι **σχεδιάστηκε και δεν
χτίστηκε**· εδώ καταγράφεται ό,τι **υπάρχει και δουλεύει, αλλά θα μπορούσε να
γίνει καλύτερα** — ασφάλεια, ορθότητα, απόδοση, ποιότητα διαδικασίας. Κάθε
εύρημα προέρχεται από πραγματική ανάγνωση του κώδικα (αναφορές `αρχείο:γραμμή`),
όχι από γενικές συμβουλές.

Ετικέτες προσπάθειας: **[εύκολο]** ώρες, **[μέτριο]** 1-2 συνεδρίες,
**[μεγάλο]** ξεχωριστό feature track.

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

### Α5. ESP32-CAM HTTP API εντελώς ανοιχτό στο LAN **[εύκολο]**
`/stream`, `/capture`, `GET|DELETE /event/<id>` (`cam_esp32.ino:195-198`)
δεν έχουν κανένα auth — οποιοσδήποτε στο LAN βλέπει τη κάμερα και μπορεί να
**διαγράψει** αποθηκευμένα γεγονότα κίνησης. Ελάχιστη βελτίωση: ένα shared
token (query param ή header) γνωστό σε Pi+εφαρμογή, έστω hardcoded στο ίδιο
`secrets.h` του Α1. Δεν είναι πλήρης λύση, αλλά ανεβάζει το κόστος από
"μηδέν" σε "χρειάζεσαι το token".

### Α6. Αχρησιμοποίητος WebSocket listener 9001 **[έγινε]**
Κανένας client δεν τον χρησιμοποιούσε πια (`docs/technical/05-mqtt-broker.md §3`).
Αφαιρέθηκε το listener block από το `pi/mosquitto/mosquitto.conf`. Στο ίδιο
πέρασμα βρέθηκε και δεύτερο, ορφανό σημείο για το ίδιο πράγμα:
`pi/avahi/greenhouse-mqtt.service` διαφήμιζε mDNS για αυτή τη θύρα αλλά
**δεν το εγκαθιστούσε ποτέ το `install.sh`** — διαγράφηκε κι αυτό.

---

## Β. Ορθότητα / Αξιοπιστία

### Β1. Το echo-suppression του HiveMQ bridge καταπίνει νόμιμες επαναλήψεις **[μέτριο]**
Το `_last_seen` cache (`hivemq_bridge.py:27,42-45`) μπλοκάρει ένα μήνυμα αν
το payload του είναι **ίδιο** με το τελευταίο που πέρασε από το ίδιο topic.
Αυτό σταματά τον βρόχο ηχούς, αλλά έχει παρενέργεια: μια **γνήσια**
επαναδημοσίευση ίδιας τιμής (π.χ. θερμοκρασία που μετρήθηκε ίδια δύο
συνεχόμενες φορές, ή retained republish των `rules/current` με ίδιο
περιεχόμενο) επίσης απορρίπτεται και δεν φτάνει ποτέ στην άλλη πλευρά.
Για αισθητήρες με συνεχείς διακυμάνσεις είναι σπάνιο· για config topics
είναι υπαρκτό. Καλύτερη λύση: αντί για σύγκριση payload, διάκριση
κατεύθυνσης — π.χ. σύντομο χρονικό παράθυρο suppression (αγνόησε το ίδιο
payload μόνο για ~2s μετά την προώθηση) ώστε η ηχώ να κόβεται αλλά μια
πραγματική επανάληψη λεπτά αργότερα να περνά.

### Β2. Διαρροή μνήμης στο reassembly των live frames της εφαρμογής **[εύκολο]**
`_liveFrameBuffers` (`greenhouse_repository.dart:37,243`) κρατά buffer ανά
`frame_id` μέχρι να φτάσουν **όλα** τα chunks. Αν χαθεί έστω ένα chunk
(θόρυβος, remote σύνδεση μέσω HiveMQ), ο buffer εκείνου του frame **μένει
για πάντα** — σε ένα μακρύ lossy live session συσσωρεύονται ημιτελή frames
στη μνήμη. Πρόταση: eviction κάθε buffer παλαιότερου από 1-2 νεότερα
`frame_id` (τα frames είναι διατεταγμένα — ένα ημιτελές παλιό frame δεν θα
ολοκληρωθεί ποτέ και δεν έχει καν αξία προβολής πια).

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

### Β4. Καμία ρητή επανασύνδεση WiFi στη γέφυρα μετά το boot **[εύκολο]**
Το `bridge_esp32.ino` κάνει blocking WiFi connect μόνο στο `setup()`. Αν το
router επανεκκινήσει αργότερα, η επανασύνδεση αφήνεται στο implicit
auto-reconnect του Arduino core — δεν υπάρχει έλεγχος `WiFi.status()` στο
`loop()` ούτε λογική ανάκαμψης αν το auto-reconnect κολλήσει. Δεδομένου ότι
η γέφυρα είναι το rank-0 anchor όλου του mesh, ένα ρητό
"αν αποσυνδεδεμένο για >X δευτ. → `WiFi.reconnect()` / restart" είναι φτηνή
ασφάλιση, στο ίδιο πνεύμα με το non-blocking MQTT reconnect που ήδη
χτίστηκε προσεκτικά (`docs/technical/04-bridge-gateway.md §4`).

### Β5. Η σάρωση καναλιού των edge nodes δένει με το SSID του router **[μέτριο]**
Κάθε edge node βρίσκει το ESP-NOW κανάλι σαρώνοντας για το hardcoded
`WIFI_SSID` του σπιτιού (`edge_node_esp32_c3.ino:22,39-45`). Αν ο χρήστης
μετονομάσει το router του, **όλοι οι κόμβοι θέλουν reflash** — παρόλο που οι
ίδιοι δεν συνδέονται ποτέ στο WiFi. Εναλλακτική χωρίς SSID εξάρτηση: σάρωση
των 13 καναλιών ακούγοντας για το ίδιο το beacon της γέφυρας (rank 0,
`MESH_MAGIC`) — η γέφυρα είναι ούτως ή άλλως η μόνη που πραγματικά χρειάζεται
να ξέρει το δίκτυο. Δένει τους κόμβους στο δικό μας σύστημα αντί σε ξένη
υποδομή.

### Β6. Ψευδές "offline" σε σειρά αποτυχιών DHT **[εύκολο]**
Τεκμηριωμένος περιορισμός στο ίδιο το firmware (`bridge_esp32.ino:96-99`):
η ζωντάνια κόμβου βασίζεται μόνο σε άφιξη **δεδομένων**, οπότε ένας κόμβος
που ζει και κάνει beacon κανονικά αλλά έχει διαδοχικά NaN από τον DHT
δηλώνεται ψευδώς offline. Φτηνή λύση συμβατή με το υπάρχον σχήμα: όταν η
μέτρηση αποτύχει, ο κόμβος να στέλνει το πακέτο με sentinel τιμές (π.χ.
NaN encoded) αντί να μη στέλνει τίποτα — η γέφυρα ανανεώνει το lastSeen
και απλά δεν δημοσιεύει τιμές.

### Β7. Σιωπηλά `catch (_) {}` στο repository της εφαρμογής **[εύκολο]**
Πολλαπλά σημεία στο `greenhouse_repository.dart` (π.χ. 111, 116, 122, 126,
250) καταπίνουν parse errors ολοκληρωτικά. Ένα κακοσχηματισμένο payload
(π.χ. μετά από μελλοντική αλλαγή σχήματος στο Pi) θα εξαφανιζόταν χωρίς
ίχνος, κάνοντας το debugging «γιατί δεν φαίνονται τα rules;» άσκοπα
δύσκολο. Πρόταση: `debugPrint` σε debug builds τουλάχιστον (και δες Δ2 για
τη γενικότερη τακτοποίηση logging).

---

## Γ. Απόδοση

### Γ1. Motion diff σε καθαρή Python στο Pi Zero W **[εύκολο]**
`motion.diff_score()` (`pi/shared/motion.py:27`) κάνει
`sum(abs(p - c) for p, c in zip(...))` πάνω σε 4.800 pixels σε ερμηνευμένη
Python, κάθε 3 δευτερόλεπτα, σε single-core ARMv6 1GHz. Με PIL που ήδη
είναι dependency: `ImageChops.difference(img1, img2)` + `histogram()` κάνει
τον ίδιο υπολογισμό σε C — τάξεις μεγέθους λιγότερο CPU στο πιο αδύναμο
μηχάνημα του συστήματος. Ίδιο αποτέλεσμα, ~5 γραμμές αλλαγή, τα υπάρχοντα
tests (`test_motion.py`) επιβεβαιώνουν ισοδυναμία.

### Γ2. Fallback σειρά σύνδεσης όταν είσαι εκτός σπιτιού **[εύκολο]**
`_attempt()` (`mqtt_connection.dart:35-50`) δοκιμάζει **πάντα** πρώτα το
LAN host με 5s timeout πριν το remote. Όταν ο χρήστης είναι εκτός σπιτιού,
κάθε (επανα)σύνδεση πληρώνει 5 χαμένα δευτερόλεπτα σε ένα host που δεν θα
απαντήσει ποτέ. Πρόταση: θυμήσου ποιο host πέτυχε τελευταίο
(`flutter_secure_storage`/prefs) και δοκίμασε αυτό πρώτα — το κόστος λάθους
είναι συμμετρικό, το κέρδος είναι 5s στο κοινό σενάριο.

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

### Δ2. debugPrint / logging τακτοποίηση **[εύκολο]**
Ήδη στο `TODO.md` (housekeeping) — αναφέρεται εδώ για πληρότητα μαζί με το
Β7: αντί απλής διαγραφής των 7 `debugPrint`, ένα ενιαίο μικρό log helper με
kDebugMode guard κρατά τη διαγνωστική αξία χωρίς θόρυβο σε release.

### Δ3. Νεκρά/παραπλανητικά αρχεία **[εύκολο]**
- `pi/mosquitto/setup_tls.sh` — απαιτεί «tailscale-ip» όρισμα, από την
  εγκαταλελειμμένη Tailscale εποχή· έχει αντικατασταθεί πλήρως από
  `gen_certs.sh` + `install.sh` (που δεν το καλεί πουθενά). Να διαγραφεί —
  όποιος το τρέξει κατά λάθος θα πάρει διπλά/ασύμβατα certs.
- Η `ExecStartPost` iptables γραμμή του portal.service (βλ. Α2).
- Checkbox state στα plan αρχεία: και τα 11 plans έχουν 0/N τσεκαρισμένα
  κουτιά ενώ τα 9 είναι υλοποιημένα (βλ. `TODO.md` Verification notes) —
  είτε να τσεκαριστούν αναδρομικά είτε να μπει "Status: DONE" header σε
  κάθε ολοκληρωμένο plan, ώστε το `- [ ]` να ξαναγίνει αξιόπιστο σήμα.
- `HANDOFF.md` — δύο τεκμηριωμένα stale σημεία (βλ. `TODO.md` §2).

### Δ4. Ο simulator ως προαιρετικό service **[εύκολο]**
Σήμερα τρέχει μόνο ως transient `systemd-run` unit που χάνεται σε reboot
(`HANDOFF.md` Quick Start) — έχει ήδη μπερδέψει προηγούμενη συνεδρία
(«γιατί είναι stale το ιστορικό;»). Ένα κανονικό
`greenhouse-simulator.service`, disabled by default (`systemctl enable`
μόνο σε demo units), κάνει την κατάσταση ρητή αντί για προφορική γνώση.

### Δ5. Topic για αισθητήρα φωτός χωρίς αισθητήρα **[σημείωση, όχι δράση]**
Ο recorder κάνει subscribe στο `greenhouse/+/light/lux` και ο simulator το
δημοσιεύει, αλλά κανένα πραγματικό firmware δεν στέλνει φωτεινότητα (το
`SensorPacket` έχει μόνο temp/humidity/soil). Είτε είναι σκόπιμη
προετοιμασία για μελλοντικό αισθητήρα (οπότε ΟΚ ως έχει), είτε αξίζει μια
γραμμή στο `TODO.md` ως σχεδιαζόμενο hardware — να αποφασιστεί συνειδητά.

---

## Προτεινόμενη σειρά (αν γίνονταν όλα)

1. **Δ1 CI** — προστατεύει όλα τα υπόλοιπα βήματα.
2. **Α1 secrets + rotation** — όσο νωρίτερα, τόσο μικρότερη η έκθεση.
3. **Α2, Α6, Δ3** — φτηνές διορθώσεις μιας συνεδρίας μαζί.
4. **Α4 TLS pinning + Γ2 host preference** — app-side ζευγάρι, μαζί με το
   PIN spec του `TODO.md` §1 συναποτελούν τη «σκλήρυνση ζεύξης».
5. **Β1-Β7** κατά περίπτωση, με προτεραιότητα Β2 (leak) και Β3 (ορατό στον
   χρήστη) πριν το επόμενο field deployment.
