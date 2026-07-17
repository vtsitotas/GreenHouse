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
- **New authentication/PIN for `/pair`.** Kept at the same trust level as
  today's WiFi-setup form (physical/RF proximity to the hotspot is the
  access control) — see Security section.

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
4. _discover(): GET http://greenhouse.local/pair
   (mDNS name resolution ή DNS-SD PTR→SRV→A fallback, πάνω στο
   192.168.4.0/24 subnet — ίδιος μηχανισμός με σήμερα)
5. portal.py pair(): _ap_mode() == True → παράλειψη 600s timer,
   επιστροφή credentials άμεσα
6. Εφαρμογή αποθηκεύει ConnectionConfig (flutter_secure_storage),
   συνδέεται MQTT TLS :8883 πάνω από το ίδιο subnet
7. Dashboard live — μόνιμα, όσο το κινητό παραμένει στο hotspot
```

## Fault Handling & Reliability

| Σενάριο | Συμπεριφορά |
|---|---|
| Κινητό βγαίνει εκτός εμβέλειας hotspot | Ίδιο υπάρχον reconnect/backoff της `MqttConnection` (`docs/technical/13-mobile-app.md §4`) — καμία αλλαγή |
| Δεύτερη/τρίτη συσκευή θέλει να ζευγαρώσει | `/pair` παραμένει ανοιχτό επ' αόριστον σε AP mode πλέον — καμία ανάγκη restart/SSH |
| Χρήστης προσπαθεί remote/HiveMQ πρόσβαση | Αναμενόμενα αδύνατο (καμία ανοδική internet σύνδεση) — η εφαρμογή θα δείχνει μόνιμα `local`/`offline`, ποτέ `remote`. Καμία αλλαγή στη λογική σύνδεσης χρειάζεται — απλά ο δεύτερος host (`config.remoteHost`) δεν θα απαντήσει ποτέ, το υπάρχον σειριακό fallback ήδη το χειρίζεται σωστά |
| Πολλαπλές ταυτόχρονες συσκευές πάνω στο ίδιο hotspot | Το NetworkManager AP (`ipv4.method shared`) ήδη υποστηρίζει πολλαπλούς clients με DHCP — καμία αλλαγή |

## Security

Δεν αλλάζει το επίπεδο έκθεσης σε σχέση με σήμερα — απλά το εξισώνει
μεταξύ των δύο AP-mode endpoints:

| Endpoint | Πριν | Μετά |
|---|---|---|
| `POST /connect` (WiFi form) | Χωρίς όριο χρόνου, μόνο `_ap_mode()` gate | Αμετάβλητο |
| `GET /pair` σε AP mode | 600s όριο από boot | **Χωρίς όριο** όσο `_ap_mode()` |
| `GET /pair` σε STA mode | 600s όριο από boot | Αμετάβλητο |

Ρητά αποδεκτό όριο (ίδιο πνεύμα με το υπάρχον backlog item στο
`HANDOFF.md` περί ανεπιβεβαίωτου `/pair`): η μοναδική «αυθεντικοποίηση»
παραμένει η φυσική/ραδιοφωνική εγγύτητα στο ανοιχτό hotspot — κανείς εκτός
εμβέλειας δεν μπορεί να φτάσει ούτε στο `/connect` ούτε στο `/pair`.

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

## Files Touched

| Αρχείο | Αλλαγή |
|---|---|
| `pi/portal/portal.py` | `pair()`: το 600s timer εφαρμόζεται μόνο όταν `not _ap_mode()` |
| `app/lib/screens/pairing/pairing_screen.dart` | Νέο κουμπί "Σύνδεση απευθείας" — καλεί το ήδη υπάρχον `_discover()` |
| (πιθανό, ανοιχτό στο implementation planning) νέα οθόνη επιλογής πριν την pairing screen | UX ροή "Σπιτικό WiFi" vs "Απευθείας σύνδεση" |

## Placeholder / Consistency Check

Κανένα TBD δεν απομένει στο βασικό μηχανισμό (η αλλαγή στο `portal.py`
είναι πλήρως προσδιορισμένη). Ανοιχτό σημείο για implementation planning:
ακριβής θέση/wording του νέου κουμπιού στο UI flow — σχεδιαστική
απόφαση, όχι τεχνικό κενό.
