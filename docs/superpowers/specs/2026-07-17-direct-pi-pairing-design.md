# Direct-to-Pi Pairing (Skip Home WiFi) — Design Spec

**Date:** 2026-07-17
**Status:** Proposed — needs approval before implementation planning

## Background

Today, first-time setup requires giving the Pi a home WiFi network's
SSID/password (`POST /connect`/`/api/connect`, `pi/portal/portal.py:169-196`)
before the app can ever pair with it — the Pi reboots, joins that network
(STA mode), and only then does `/pair` become the "normal" way in.

This blocks a real deployment scenario: a greenhouse with no ISP WiFi
available at all (rural site, no router in range). Investigation during
this session found the underlying protocol already supports pairing
**directly against the Pi's own setup hotspot**, without ever configuring
a home network:

- `/pair` (`portal.py:198-217`) has **no** `_ap_mode()` gate — it is
  reachable today during AP mode too, gated only by the 600s
  `_PAIR_WINDOW` timer since service start.
- `avahi-daemon` (mDNS/DNS-SD) answers on every active interface
  regardless of AP/STA state — `greenhouse.local` resolves the same way
  over the hotspot's `192.168.4.0/24` subnet as over a home LAN (see
  `docs/technical/09-setup-portal.md §8`).
- Mosquitto's TLS listener (8883) binds all interfaces, not loopback-only
  — reachable from a phone connected to the hotspot.
- The recorder/weather pipeline never depended on internet or STA mode in
  the first place (`docs/technical/07-recorder-service.md`) — it only
  needs the local Mosquitto broker, which runs regardless.

So the only missing piece is a **UI path in the app** that skips the
WiFi-entry step and pairs immediately over whatever network the phone is
currently on. The user explicitly does not want a permanent dual AP+STA
mode (ruled out in conversation) — this design keeps the Pi's existing
single-radio AP-or-STA behavior unchanged and just adds a way to use the
AP as the **permanent** mode by choice.

## Goals

1. Let a user pair the app directly against the Pi's own setup hotspot
   (`Greenhouse-XXXX`), with zero home WiFi ever configured.
2. Reuse the existing `/pair`, MQTT 8883, and `_discover()` mDNS/DNS-SD
   logic exactly as they work today — no new discovery protocol.
3. Everything that already works LAN-locally (dashboard, control,
   history, camera) must keep working identically over the hotspot
   subnet — no special-casing needed in the data pipeline, since it never
   depended on STA/internet.
4. Let the pairing window be usable indefinitely in this mode, so a
   second/third device (or a re-pair after the app is reinstalled) doesn't
   require SSH access — which a WiFi-less deployment's end user won't
   have.
5. Require a **PIN** (proof of physical possession) before `/pair` ever
   hands out real credentials, with brute-force protection — this becomes
   necessary precisely because Goal 4 removes the only mitigation `/pair`
   had (the 600s window) for AP mode, and mDNS/DNS-SD discovery itself has
   no authenticity guarantee (see Security section).

## Non-goals

- **Permanent dual AP+STA mode.** Explicitly ruled out — this design
  changes nothing about the Pi's single-radio AP-or-STA behavior. "Direct
  connect" simply means the user never submits home WiFi credentials, so
  the Pi stays in its existing AP mode forever (nothing new to build on
  the radio-management side).
- **Remote/HiveMQ access while in this mode.** With no STA WiFi, the Pi
  has no upstream internet route at all — the HiveMQ Cloud bridge
  (`docs/technical/08-cloud-bridge.md`) cannot connect. The app will show
  `ConnectionStatus.local` only, never `.remote`. This is an inherent
  consequence of "no ISP available," not a bug this spec fixes.
- **LoRa/cellular long-range connectivity.** Separate future track,
  discussed but out of scope here (see `docs/EDGE_NODE_POWER_OPTIMIZATION.md`
  for the analogous edge-node power track; no equivalent doc exists yet
  for Pi-side long-range backhaul).
- **QR-code pairing changes.** `qr_scan_screen.dart`'s existing flow scans
  a QR code that already contains full credentials directly (no network
  discovery involved) — it's already immune to the mDNS-spoofing concern
  this spec's PIN closes, since scanning requires physical access to a
  printed/displayed code, not network reachability. Untouched here.
- **Per-device/per-IP rate limiting.** The lockout below is a single
  global counter, not tracked per source IP — see Security section for
  why that's the simpler, sufficient choice for this threat model.

## Architecture

### App side — `pairing_screen.dart`

Add a new entry point button, e.g. **"Σύνδεση απευθείας (χωρίς σπιτικό
WiFi)"**, shown alongside "Find my greenhouse" / "Scan QR code". Tapping
it calls the **same, unmodified** `_discover()` method
(`pairing_screen.dart:77-117`) — no new discovery code. The only reason
this needs its own button rather than just relying on the existing "Find
my greenhouse" flow is UX clarity: a first-time user standing next to a
greenhouse with no home WiFi shouldn't be funneled through a WiFi-entry
form that doesn't apply to them.

Placement: this button makes most sense reachable from wherever the app
currently sends a first-time user before/instead of the WiFi-config
step — likely a new choice screen ("Σπιτικό WiFi" vs "Απευθείας
σύνδεση") shown before the existing pairing screen, or a secondary button
directly on the pairing screen if that screen is already reachable
without having gone through WiFi setup. Exact screen flow is an
implementation-planning decision, not fixed here.

### Portal side — `/pair` window policy

Current behavior (`portal.py:198-201`):
```python
if time.time() - _START_TIME > _PAIR_WINDOW:   # 600s
    return jsonify({"error": "..."}), 403
```

Proposed change: skip the timer entirely while genuinely in AP mode
(`_ap_mode() == True`, i.e. `.wifi_configured` was never written):
```python
if not _ap_mode() and time.time() - _START_TIME > _PAIR_WINDOW:
    return jsonify({"error": "..."}), 403
```

Rationale: in STA mode, the 600s window exists to limit the blast radius
of the unauthenticated credential handoff (`HANDOFF.md` backlog item) —
anyone on the home LAN within 10 minutes of boot. In AP mode, the
equivalent threat model is already "anyone who can join the open hotspot"
— which has **no time limit today** on the WiFi-setup form itself
(`POST /connect` has no window check). Applying the same no-timer policy
to `/pair` while in AP mode doesn't introduce a new class of exposure;
it just makes the two AP-mode endpoints consistent. This also directly
satisfies Goal 4 (no SSH needed to re-open pairing) — `/pair` is
permanently reachable as long as the Pi is in AP mode.

### PIN authentication & brute-force protection

**Πρόβλημα που κλείνει:** χωρίς αυτό, το να αφαιρεθεί το 600s timer σε AP
mode (§Portal side παραπάνω) θα σήμαινε ότι `/pair` δίνει credentials σε
**οποιονδήποτε** βρει τη συσκευή μέσω mDNS, επ' αόριστον — και το ίδιο το
mDNS/DNS-SD δεν έχει καμία αυθεντικοποίηση (οποιαδήποτε συσκευή στο ίδιο
δίκτυο/hotspot μπορεί να απαντήσει σε ένα mDNS ερώτημα, spoofing). Το PIN
προσθέτει proof-of-physical-possession: μόνο όποιος έχει φυσική πρόσβαση
στη μονάδα (διαβάζει την ετικέτα πάνω της) μπορεί να ολοκληρώσει το
ζευγάρωμα, ανεξάρτητα από το ποιος απαντά στο δίκτυο.

**Διαχωρισμός endpoint σε δύο βήματα** (η σημερινή `GET /pair` επιστρέφει
credentials σε ένα βήμα, χωρίς PIN — αυτό αλλάζει):

```
GET  /pair           -> {"found": true}   (μόνο επιβεβαίωση ύπαρξης, ΚΑΝΕΝΑ secret)
POST /pair/confirm   -> body {"pin": "123456"}
                      -> 200 + credentials JSON (ίδιο σχήμα με σήμερα) αν σωστό PIN
                      -> 401 αν λάθος PIN
                      -> 429 αν κλειδωμένο (πάρα πολλές λάθος προσπάθειες)
```

Το `GET /pair` παραμένει χρήσιμο για το πρώτο βήμα του `_discover()`
(απλή επιβεβαίωση "υπάρχει greenhouse εδώ;"), αλλά δεν αποκαλύπτει πια
τίποτα ευαίσθητο — το πραγματικό secret-fetch μετακινείται στο
`POST /pair/confirm`, το οποίο είναι το μόνο σημείο που χρειάζεται
rate-limiting.

**Παραγωγή PIN:** `first_boot.sh` παράγει ένα **6-ψήφιο αριθμητικό PIN**
από `/dev/urandom` στην ίδια στιγμή που παράγει το MQTT password
(`first_boot.sh:19`), γράφεται σε νέο πεδίο `pair_pin` στο
`device.json`. Ίδιο μοτίβο μοναδικότητας-ανά-μονάδα με το ήδη υπάρχον
password/certs (`docs/technical/10-security.md §7`).

**Εμφάνιση στον χρήστη:** φυσική ετικέτα πάνω στη μονάδα, τυπωμένη κατά
την κατασκευή (production requirement — προσθήκη στο βήμα μαζικής
παραγωγής του `INSTRUCTIONS.md` Part 3). Για bench-testing σε αυτή τη
φάση (πριν υπάρξουν ετικέτες), αρκεί `cat /etc/greenhouse/device.json`
μέσω SSH — αποδεκτό προσωρινό κενό, ίδιο πνεύμα με άλλα ήδη τεκμηριωμένα
"thesis-scope, όχι πλήρες production hardening" σημεία του project.

**Rate limiting / lockout** (`portal.py`, in-memory module-level state,
ίδιο μοτίβο με το υπάρχον `_START_TIME`):
```python
MAX_PAIR_ATTEMPTS = 5
_pair_fail_count = 0
_pair_locked = False

@app.route("/pair/confirm", methods=["POST"])
def pair_confirm():
    global _pair_fail_count, _pair_locked
    if _pair_locked:
        return jsonify({"error": "locked — restart the Pi to try again"}), 429
    pin = (request.get_json(silent=True) or {}).get("pin", "")
    if pin != _load_config()["pair_pin"]:
        _pair_fail_count += 1
        if _pair_fail_count >= MAX_PAIR_ATTEMPTS:
            _pair_locked = True
        time.sleep(1)   # throttle — κάνει ακόμα και τις 5 προσπάθειες αργές για script
        return jsonify({"error": "invalid PIN"}), 401
    _pair_fail_count = 0
    return jsonify({...})   # ίδιο σχήμα με το σημερινό /pair response
```

**Γιατί global counter, όχι per-IP:** απλούστερος κώδικας, και το
threat model εδώ είναι ήδη "επιτιθέμενος μέσα στο ίδιο μικρό LAN/hotspot"
— ένα global lockout μετά από 5 λάθος προσπάθειες (από **οποιαδήποτε**
πηγή) είναι πιο συντηρητικό από per-IP (που θα μπορούσε θεωρητικά να
παρακαμφθεί) TCP-level. Trade-off: ένας νόμιμος χρήστης που κάνει typo το
PIN 5 φορές κλειδώνει το endpoint για όλους μέχρι restart — σπάνιο,
ανακτήσιμο συμβάν, ίδιο μοτίβο με το ήδη υπάρχον "restart για να ξανανοίξει
το pairing window".

**Reset του lockout:** μόνο μέσω `systemctl restart greenhouse-portal`
(in-memory state, όχι persisted σε δίσκο) — αν ο χρήστης δεν έχει SSH
(WiFi-less deployment), αυτό σημαίνει φυσική επανεκκίνηση όλου του Pi
(κόψιμο ρεύματος) αντί για επιλεκτικό restart υπηρεσίας.

### No changes needed

- `ap_up.sh` / `greenhouse-ap.service` — the AP already runs indefinitely
  whenever `.wifi_configured` is absent; nothing to build here.
- `avahi`, Mosquitto listener bindings, recorder/weather/portal data
  pipeline — all already interface-agnostic (see Background).
- MQTT credentials/TLS provisioning (`first_boot.sh`) — unaffected,
  already runs on first boot regardless of eventual WiFi mode.

## Wire-level flow

```
1. Boot, no .wifi_configured → greenhouse-ap.service starts hotspot
   "Greenhouse-XXXX" (ap_up.sh, unchanged)
2. Χρήστης συνδέεται στο hotspot, ανοίγει την εφαρμογή
3. Pairing flow → "Σύνδεση απευθείας"
4. _discover(): GET http://greenhouse.local/pair → {"found": true}
   (mDNS name resolution ή DNS-SD PTR→SRV→A fallback, πάνω στο
   192.168.4.0/24 subnet — ίδιος μηχανισμός με σήμερα, αλλά ΧΩΡΙΣ
   secrets σε αυτό το βήμα πια)
5. Εφαρμογή δείχνει: "Βρέθηκε το θερμοκήπιο! Εισάγετε το PIN από την
   ετικέτα της συσκευής"
6. Χρήστης πληκτρολογεί PIN → POST http://<host>/pair/confirm
   {"pin": "..."}
7. portal.py pair_confirm(): σωστό PIN (και όχι κλειδωμένο) →
   επιστροφή credentials· λάθος PIN → 401, μετρητής αυξάνεται,
   κλείδωμα μετά από 5
8. Εφαρμογή αποθηκεύει ConnectionConfig (flutter_secure_storage),
   συνδέεται MQTT TLS :8883 πάνω από το ίδιο subnet
9. Dashboard live — μόνιμα, όσο το κινητό παραμένει στο hotspot
```

## Fault Handling & Reliability

| Σενάριο | Συμπεριφορά |
|---|---|
| Κινητό βγαίνει εκτός εμβέλειας hotspot | Ίδιο υπάρχον reconnect/backoff της `MqttConnection` (`docs/technical/13-mobile-app.md §4`) — καμία αλλαγή |
| Δεύτερη/τρίτη συσκευή θέλει να ζευγαρώσει | `/pair` παραμένει ανοιχτό επ' αόριστον σε AP mode πλέον — καμία ανάγκη restart/SSH |
| Χρήστης προσπαθεί remote/HiveMQ πρόσβαση | Αναμενόμενα αδύνατο (καμία ανοδική internet σύνδεση) — η εφαρμογή θα δείχνει μόνιμα `local`/`offline`, ποτέ `remote`. Καμία αλλαγή στη λογική σύνδεσης χρειάζεται — απλά ο δεύτερος host (`config.remoteHost`) δεν θα απαντήσει ποτέ, το υπάρχον σειριακό fallback ήδη το χειρίζεται σωστά |
| Πολλαπλές ταυτόχρονες συσκευές πάνω στο ίδιο hotspot | Το NetworkManager AP (`ipv4.method shared`) ήδη υποστηρίζει πολλαπλούς clients με DHCP — καμία αλλαγή |
| Χρήστης κάνει typo το PIN 5 φορές | `/pair/confirm` κλειδώνει (429) για όλους μέχρι restart — φυσική επανεκκίνηση απαραίτητη αν δεν υπάρχει SSH πρόσβαση |
| Επιτιθέμενος προσπαθεί brute-force PIN | Μόνο 5 προσπάθειες διαθέσιμες πριν το lockout (από 1.000.000 πιθανούς συνδυασμούς) — πρακτικά μηδενίζει την επιτυχία, ενισχυμένο από το throttle 1s ανά αποτυχία |
| Επιτιθέμενος spoofάρει το mDNS response | Μπορεί να απαντήσει ψεύτικα "βρέθηκε" στο `GET /pair`, αλλά δεν έχει το πραγματικό PIN — `/pair/confirm` στον δικό του fake server δεν έχει νόημα να εξαπατήσει, αφού ο χρήστης βλέπει ό,τι επιστρέφει *αυτός* (θα χρειαζόταν να ξέρει το πραγματικό PIN για να παραδώσει αληθοφανή credentials πίσω) — το ρίσκο περιορίζεται σε άρνηση υπηρεσίας (DoS), όχι σε κλοπή credentials |

## Security

| Endpoint | Πριν | Μετά |
|---|---|---|
| `POST /connect` (WiFi form) | Χωρίς όριο χρόνου, μόνο `_ap_mode()` gate | Αμετάβλητο |
| `GET /pair` σε AP mode | 600s όριο, επιστρέφει credentials | **Χωρίς όριο**, επιστρέφει μόνο `{"found": true}` — κανένα secret |
| `GET /pair` σε STA mode | 600s όριο, επιστρέφει credentials | 600s όριο, επιστρέφει μόνο `{"found": true}` — **ίδια αλλαγή εφαρμόζεται και εδώ**, βλ. σημείωση παρακάτω |
| `POST /pair/confirm` (νέο) | — | Απαιτεί σωστό PIN· 5 προσπάθειες, μετά lockout μέχρι restart |

**Σημαντικό:** η αλλαγή "GET /pair δεν επιστρέφει πια secrets απευθείας"
εφαρμόζεται **και στη STA mode ροή**, όχι μόνο στο AP-direct-connect
σενάριο αυτού του spec — αλλιώς το ίδιο mDNS-spoofing κενό θα παρέμενε
ανοιχτό στην κανονική, καθημερινή ζεύξη μέσω σπιτικού WiFi. Το PIN
προστίθεται ως γενική σκλήρυνση του `/pair` μηχανισμού, με το AP-mode
timer-removal (§Goal 4) απλά να είναι ο λόγος που έγινε **επείγον** να
προστεθεί τώρα.

Πριν από αυτή την αλλαγή, η μοναδική «αυθεντικοποίηση» ήταν η
φυσική/ραδιοφωνική εγγύτητα στο δίκτυο (ίδιο πνεύμα με το backlog item
στο `HANDOFF.md` περί ανεπιβεβαίωτου `/pair`) — πλέον προστίθεται και
proof-of-possession μέσω PIN, χωρίς να αφαιρείται η προηγούμενη γραμμή
άμυνας (δικτυακή εγγύτητα παραμένει προαπαιτούμενο, το PIN είναι
επιπλέον επίπεδο).

## Testing / Validation

Δεν υπάρχει αυτοματοποιημένο test harness για τα Flask routes σήμερα
πέρα από `pi/tests/test_portal_history.py` (μόνο `/api/history*`). Bench
plan:

1. Νέα μονάδα, χωρίς ποτέ `.wifi_configured` — επιβεβαίωση ότι
   `GET /pair` επιστρέφει 200 ακόμα και >600s μετά την εκκίνηση.
2. Κινητό συνδεδεμένο στο `Greenhouse-XXXX` — πάτημα νέου κουμπιού,
   επιβεβαίωση επιτυχούς ζεύξης χωρίς ποτέ να ζητηθεί WiFi.
3. Επιβεβαίωση ζωντανού dashboard (MQTT 8883 πάνω από `192.168.4.0/24`).
4. Επιβεβαίωση ότι η κανονική ροή WiFi setup (`POST /connect` →
   reboot → STA mode) παραμένει αναλλοίωτη.
5. Δεύτερη συσκευή ζευγαρώνει αργότερα (π.χ. την επόμενη μέρα) χωρίς
   restart της υπηρεσίας.
6. Σωστό PIN → `POST /pair/confirm` επιστρέφει credentials, ταιριάζει
   ακριβώς με το σχήμα του σημερινού `/pair`.
7. Λάθος PIN 4 φορές → κάθε φορά 401, μετρητής αυξάνεται· 5η φορά →
   429 lockout.
8. Μετά το lockout, ακόμα και το **σωστό** PIN απορρίπτεται (429) μέχρι
   `systemctl restart greenhouse-portal`.
9. Επιβεβαίωση ότι `GET /pair` (και στις δύο καταστάσεις AP/STA) δεν
   επιστρέφει ποτέ πια `username`/`password`/`tls_fingerprint` — μόνο
   `{"found": true}`.
10. Confirm ότι η ροή QR-code (`qr_scan_screen.dart`) παραμένει
    αναλλοίωτη — δεν περνάει ποτέ από `/pair`/`/pair/confirm`.

## Files Touched

| Αρχείο | Αλλαγή |
|---|---|
| `pi/portal/portal.py` | `pair()`: το 600s timer εφαρμόζεται μόνο όταν `not _ap_mode()`· επιστρέφει πλέον μόνο `{"found": true}`, όχι credentials. Νέο route `pair_confirm()` (`POST /pair/confirm`) με το PIN check + lockout λογική |
| `pi/scripts/first_boot.sh` | Παραγωγή νέου `pair_pin` (6-ψήφιο, `/dev/urandom`), προσθήκη στο `device.json` |
| `app/lib/screens/pairing/pairing_screen.dart` | Νέο κουμπί "Σύνδεση απευθείας" (καλεί `_discover()` αμετάβλητο)· νέο βήμα UI για εισαγωγή PIN μετά την ανακάλυψη· `_applyPair()` προσαρμόζεται να καλεί `POST /pair/confirm` αντί να διαβάζει credentials απευθείας από το `GET /pair` response |
| `INSTRUCTIONS.md` | Νέο βήμα στη διαδικασία μαζικής παραγωγής (Part 3): εκτύπωση ετικέτας με το PIN ανά μονάδα |
| (πιθανό, ανοιχτό στο implementation planning) νέα οθόνη επιλογής πριν την pairing screen | UX ροή "Σπιτικό WiFi" vs "Απευθείας σύνδεση" |

## Placeholder / Consistency Check

Δύο σημεία ανοιχτά για implementation planning, σχεδιαστικές αποφάσεις
όχι τεχνικά κενά:
- Ακριβής θέση/wording του νέου κουμπιού "Σύνδεση απευθείας" στο UI flow.
- Ακριβής μηχανισμός εκτύπωσης/επισύναψης της φυσικής ετικέτας PIN κατά
  τη μαζική παραγωγή (out of scope λεπτομέρεια εκτύπωσης — απαιτεί μόνο
  ότι το PIN είναι αναγνώσιμο στο `device.json` πριν το image κλωνοποιηθεί
  ή αμέσως μετά το πρώτο boot κάθε μονάδας).
