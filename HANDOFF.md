# Greenhouse IoT — Session Handoff

**Last updated:** 2026-08-22 (CSV export of history data, and test coverage
for the forecast-failure fallback path — app-only, no hardware/firmware
touched). Previous session 2026-08-16: fake sensor firmware rewritten to match
production mesh logic exactly, a MAC-reader bench utility, fleet grown to 4
sensor boards then back to 3 after a bad-antenna diagnosis, ghost MQTT topics
cleared, and a real multi-hop relay confirmed working over the air.
Previous session 2026-08-13: technical report for the thesis writeup, a new
Plant Care Profiles feature on the Dashboard, and a clean release build/install
+ live QR regeneration on the bench phone/Pi. Previous session 2026-08-12:
ghost retained topics + scaling analysis, earlier the same day plain-language
UX overhaul, real `last seen` timestamps, drag-to-pin fix, QR tool rewrite.
Previous same-day-2026-08-11 sessions: history false-alert fix + mesh map
bugfixes + git-history credential scrub. Session 2026-08-10: WiFi provisioning
+ UART bridge bring-up, full bench deploy. Camera remains **parked** since
2026-08-02 (see that TL;DR below); the 2026-07-28 security-hardening pass is
merged and live.

> ⚠️ **Every commit hash changed on 2026-08-11.** History was rewritten to
> remove leaked credentials. Any clone made before then must be **re-cloned**,
> not pulled — pulling would merge the literals back in. Pre-scrub hashes
> quoted in the older session notes below no longer resolve.

**Status:** The mesh path is now **proven end to end**, including real
multi-hop relay — the 2026-08-10 note below ("no edge node has actually
delivered a sensor reading yet") is resolved. Current fleet: bridge (`BE:80`)
+ zone2/3/4 (`9D:B0`/`6B:50`/`75:EC`), all three temporarily `sleepy=false`
for the relay test — **revert to `sleepy=true` before any battery
deployment**, they're drawing mains-level current right now. zone1 (`A1:B0`)
is benched with a confirmed bad TX antenna, not retired. UART bridge to the
Pi remains **working** (2026-07-27's "no traffic" note was a diagnostic
artifact, not the link). WiFi first-time setup (captive portal) is fixed end
to end. The bench Pi is fully deployed off current `main`, `selftest.sh`
reports 45/45.

---

## TL;DR of this session (2026-08-22 — CSV history export, forecast-fallback test coverage)

Ran from a sandbox with no `flutter`/`pytest` toolchain available at all (not
just no hardware — confirmed by trying), so this session deliberately picked
the two open `TODO.md` items that are pure Dart and don't need either:
firmware/hardware work, credential rotation, and iOS testing were all left
alone as out of scope for this environment.

**CSV export of history.** `TODO.md`'s "App feature gaps" listed this as not
started. Added an export button (share icon, next to the range label on the
History screen's chart card) that shares a CSV of the currently-loaded
`actual` points — real recorded data only, never the dashed prediction/
forecast overlay — via the OS share sheet (email, Drive, Files, etc.). New
`share_plus` dependency: checked pub.dev's version list directly rather than
guessing, since this repo's `pubspec.yaml` already carries a `vector_math`
override worked out from real SDK-constraint conflicts — picked `^10.1.2`,
the newest release still declaring `sdk: >=3.3.0 <4.0.0` (matching this
repo's declared environment exactly; later releases bump to `>=3.4.0`/
`>=3.10.0`, still fine but 10.1.2 is the widest-compatible choice). Also
pulled `share_plus`'s and `cross_file`'s actual source from pub.dev to
confirm the real `Share.shareXFiles`/`XFile.fromData` signatures rather than
assume — worth noting since `XFile.fromData`'s `name` parameter is silently
*ignored* on Android/iOS (cross_file's `io.dart`, by design), so the visible
filename actually comes from `shareXFiles`'s separate `fileNameOverrides`
parameter; missing that would have shipped a working share sheet with the
wrong filename on every real device while looking correct in review. New
`app/lib/utils/history_csv.dart` (`historyToCsv`/`historyCsvFilename`, pure
functions, unit tested) plus the button wiring in `history_screen.dart`,
wrapped in try/catch with a `SnackBar` on failure rather than letting a
share-sheet exception reach the user raw (matching the existing
`friendly_error.dart` convention from the 2026-08-12 UX pass).

**Forecast-failure fallback, now test-covered.** Same `TODO.md` section:
"No direct test exercises the forecast-timeout/failure fallback path in
`historyWithPredictionProvider`." Added a test overriding `forecastProvider`
with `Stream.error(...)`, proving the `catch (_)` in
`history_provider.dart`'s prediction logic falls through to `predictTrend`
instead of leaking the exception or leaving `predicted` empty. Covers the
*failure* half specifically, not the sibling `.timeout(Duration(seconds: 3))`
call on the same line — both paths hit the identical `catch` block, and a
true timeout test would need to actually block for 3 real seconds (no
`fake_async` dependency in this repo to fast-forward it) for one branch that
behaves identically either way, which isn't worth adding as a dependency for.

**Also checked, found already fixed:** `TODO.md`'s "Housekeeping" section
still lists `WEATHER_INTERVAL=30` as a live debug value. `git log -p` on
`pi/systemd/greenhouse-weather.service` shows it was already changed to
`1800` in a later commit — the tracked file is correct. That `TODO.md` entry
was about a *deployed* Pi still running stale config, not something fixable
in this repo's code; left as a note for whoever next redeploys the bench Pi,
not touched here.

**Not run locally — no toolchain in this sandbox.** `flutter analyze`/
`flutter test` and `pytest` are both unavailable here (confirmed, not
assumed); CI (`flutter-tests` job) runs both on every push. Read through
every new/changed file by hand afterward for the compiler this sandbox
doesn't have, including pulling the real dependency source to check exact
API signatures rather than trusting memory.

---

## TL;DR of this session (2026-08-16 — fake sensor firmware, MAC reconciliation, real relay test)

Started from "I can't solder the sensors yet, make the mesh testable without
them" and ended up field-verifying multi-hop relay for the first time.

**`firmware/fake_edge_node_esp32_c3/` was completely stale and rewritten.**
The existing file predated the fixed-channel rewrite, the deep-sleep spec, and
battery telemetry — it still scanned for a WiFi SSID that no longer applies in
the UART-bridge deployment, had no sleep cycle, and set a `light_lux` field
`SensorPacket` no longer has (would not have compiled). New version mirrors
`edge_node_esp32_c3.ino`'s mesh logic byte-for-byte (verified by diff) — same
fixed channel, same sleepy wake cycle, same RTC persistence, same battery
telemetry path — with only the DHT22/soil-ADC/battery-divider reads swapped
for an RTC-persisted, per-MAC-seeded random walk, so a fake node is
indistinguishable from a real one to the rest of the mesh. New
`firmware/mac_reader/` utility (prints a board's MAC over Serial, no mesh/
sensor code) for identifying unlabeled boards before assigning roles.

**`TRUSTED_NODES[]` reconciliation.** `docs/DEVICES.md` and `mesh_config.h`
had drifted apart and even disagreed with each other on which MAC was the
bridge. Re-verified all 4 physical sensor boards' real MACs with
`mac_reader.ino`, grew the fleet from 2 sensors to 4 (added zone3/zone4),
then diagnosed zone1 (`A1:B0`) as having a bad TX antenna — strong, stable
RSSI hearing the bridge (proves receive works) but 0/7 wake-cycle unicasts
ever got a MAC-layer ACK, even moved to point-blank range (rules out "just
too far", confirms a TX-path fault) — and pulled it from `TRUSTED_NODES[]`.
Testing continues on zone2-4. Cleared two ghost entries from the app via
`clear_retained.sh` (both brokers, verified clean): zone1's now-stale
device/zone topics, plus a `zone1_test` ghost left over from earlier bench
testing. Learned the hard way that `DRY_RUN=1 sudo bash script.sh` doesn't
actually preview anything — `sudo` doesn't pass environment variables through
by default, so the flag never reaches the script and it just runs for real.

**Real multi-hop relay confirmed working, first time ever field-tested.**
Sleepy (battery) nodes can never be adopted as a relay parent by design
(`mesh_node.h`'s `meshHandleBeacon` rejects any `MESH_FLAG_SLEEPY` beacon
before parent-selection even runs) — so zone2/3/4 were temporarily flipped to
`sleepy=false` (no firmware change needed, same image, role is looked up from
`TRUSTED_NODES[]` at boot) to make them relay-eligible. Walked a board out of
the bridge's direct range with another in between: it adopted the nearer
board as parent (rank 2, not unrouted), the bridge logged the relayed packet,
and the app's mesh map redrew the link live. Also clarified for the record:
there is no clock-synchronization algorithm in this codebase today — that's
an explicitly deferred, not-yet-designed "Phase 2" in
`docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md`. Phase 1 (what's
shipped) avoids needing sync entirely by making sleepy nodes leaf-only, which
is exactly why they had to go always-on for this test. **Remember to revert
zone2/3/4 to `sleepy=true` before any real battery deployment.**

---

## TL;DR of this session (2026-08-13 — technical report, Plant Care Profiles, live build/QR)

Three unrelated threads: a document the owner needed for the thesis writeup, a
real user-facing feature, and getting a fresh build onto the bench phone.

**New: `docs/GreenHouse_Report.docx`.** A full Greek technical report (21
chapters, table of contents, 4 architecture diagrams, 23 tables) synthesizing
everything already written — `docs/technical/`, `docs/superpowers/{specs,plans}`,
this file and its archive, plus direct reads of `pi/scripts/`, `firmware/`,
`app/lib/` for anything the existing docs didn't already cover in code-level
detail. Built with a custom Markdown→docx pipeline (`python-docx`) rather than
pasting into Word by hand, specifically so it stays regenerable. The table of
contents went through two designs: the first used a Word `TOC` field, which
only populates if the viewer actually evaluates fields (Word after an F9 or an
explicit prompt) — silently blank in anything else, which is exactly what the
owner hit. Replaced with a static table of contents built from real Word
bookmarks + internal hyperlinks (168 entries, one per chapter/section heading),
which renders immediately in any viewer with zero user action. Not committed
until this session, by earlier request.

**New feature: Plant Care Profiles, on the Dashboard.** Pick a plant per zone
(8 built-in profiles — tomato, cucumber, pepper, eggplant, lettuce, basil,
strawberry, succulent — or type custom min/max ranges) and get a colored
status badge on the zone card, a plain-language advice line when a metric is
out of range, a historical view (7d/30d average + % of time within range, via
the already-existing `historyPointsProvider` — no new backend query), and a
one-tap "add suggested rule" that opens the existing rule builder
(`rule_form_dialog.dart`) pre-filled from the breached threshold. Entirely
app-side — no Pi or firmware touched. New files: `models/plant_profile.dart`,
`services/zone_plant_store.dart` (mirrors `DeviceNamesStore`'s local-only,
single-JSON-blob-in-SharedPreferences shape exactly — which plant is in which
zone is this grower's own labelling, not fleet config, same reasoning as
device names), `utils/plant_status.dart`, `screens/dashboard/zone_care_sheet.dart`.

Found and fixed two smaller inconsistencies in the same pass, both real, both
pre-existing: `weather_card.dart`'s frost/heat/rain thresholds were bare
numeric literals (`temp < 3`, `temp > 35`, `rain > 0.1`) with no name and no
comment, unlike `zone_card.dart`'s own `kLowSoilMoisturePct` — now named
constants (`kFrostTempC`/`kHeatTempC`/`kRainThresholdMm`). And
`connection_banner.dart` had its own inline wording for `ConnectionStatus.local`
("Connected") that disagreed with Settings' (`plain_language.dart`'s
`connectionStatusLabel()`, "Connected on your home network") — same status,
two different sentences depending which screen you were on. Banner now calls
the shared function for local/remote/reconnecting, keeping only the
offline+`lastSeen` case bespoke since that needs a parameter the shared
function doesn't take.

`flutter analyze`: clean. `flutter test`: **332 passed, 0 failed** (every new
file has its own test; `zone_card_test.dart`/`weather_card_test.dart`/
`connection_banner_test.dart` extended; a new `dashboard_screen_test.dart`
added — none existed for that screen before). Two test-only bugs surfaced and
fixed along the way, not in the app itself: an existing `find.byType(Tooltip)`
assertion in `zone_card_test.dart` became ambiguous the moment a second
Tooltip (the new plant-care button) existed on the card, and a `ProviderScope`
reused across two sequential `pumpWidget` calls in one `weather_card_test.dart`
test didn't reliably pick up the second override — split into independent
single-pump tests instead of chasing the exact Riverpod-in-flutter_test
semantics.

**Build, install, and a live finding on the bench Pi.** Clean
`flutter build apk --release` (68MB) installed on the bench phone (Redmi Note
13 Pro+). First install attempt failed with `INSTALL_FAILED_USER_RESTRICTED` —
MIUI/HyperOS blocks `adb install` unless "Install via USB" is separately
enabled in Developer options (distinct from plain USB debugging, no prompt
shown until that's on); worked once the owner toggled it. Regenerated the
pairing QR against the live bench Pi over SSH (`greenhouse.local` — the
`10.215.140.202` noted on 2026-08-10 was a stale DHCP lease, it's
`10.47.153.202` now) by calling `show_qr.py`'s own `build_payload()` and saving
a PNG instead of just the terminal ASCII-art (the script has no image-output
mode of its own) — saved to the Desktop rather than only printing to a
terminal. Confirms in practice what `TODO.md §4` already had recorded: the
`app` MQTT password on this unit is still the literal placeholder `123`,
i.e. `rotate_secrets.sh` still hasn't actually been run here. Not fixed this
session (would invalidate the existing paired session on the bench phone) —
same deferral as before, just re-verified live rather than assumed.

---

## TL;DR of this session (2026-08-12 — ghost retained topics, scaling analysis)

Two things after the UX pass, both driven by the owner using the bench system.

**A retired sensor kept coming back from the dead, and the reason was not what
I first said it was.** `zone1_test` (MAC `…75EC`) reappeared as a phantom
device and a phantom dashboard zone after *three* separate clean-ups. First
diagnosis — mosquitto persistence not flushed before a reboot — was **wrong**.

The real mechanism: MQTT delivers a *live* message to an already-subscribed
client with the retain flag **cleared**; it is only set when a message is
replayed to a **new** subscription. `hivemq_bridge.py` forwards
`retain=msg.retain`, so a retained publish crosses the bridge as an ordinary
non-retained message and the far broker never stores it — **including the
empty-payload publish that means "delete"**. So clearing one broker provably
does not clear the other. Retained *state* syncs only when the bridge
(re)connects and receives the far side's retained set with `retain=1`. That is
what resurrected the ghosts: local was cleared, the bridge later reconnected,
pulled the cloud's still-dirty set down, and republished it locally *with*
retain.

Verified by controlled experiment, prediction stated before the run: a topic
planted on the cloud alone did not appear locally for minutes, then appeared
**the instant** the bridge was restarted.

New `pi/scripts/clear_retained.sh` — expands a wildcard into the concrete
topics both brokers hold, clears **cloud first then local**, SIGUSR1s mosquitto
so the deletion hits disk immediately (default autosave is 1800s, so a power
cut inside that window would restore everything), and **verifies both brokers**
instead of assuming. `DRY_RUN=1` to preview. Tested end to end against planted
ghosts including a bridge restart afterwards — the exact action that used to
bring them back. `docs/technical/08-cloud-bridge.md` gained the retain-flag
caveat.

**New doc: `docs/SCALING_AND_EXPANSION_IDEAS.md`.** Answers "why can't the user
just add more sensors?" and "what would a real product need?", grounded in the
actual code rather than generalities: the 8-device cap comes from ESP-NOW's
7-encrypted-peer limit and `meshInit()` registering every node pairwise; the
fleet-reflash requirement comes from `TRUSTED_NODES[]` being `static const`
plus any node possibly becoming any other's parent at runtime.

The unlock is end-to-end encryption with dumb relays — a relay only ever reads
`magic`/`origin_mac`/`seq`/`ttl`, so it never needs to decrypt, which removes
both the peer cap and the pairwise key distribution at once. Parent selection
is unaffected because it runs off beacons, which are already plaintext
broadcast. Two-tier keys (NetKey for cheap header auth, AppKey end-to-end)
cover the resulting DoS gap — the same split Bluetooth Mesh uses.

**Analysis only — nothing implemented, deliberately.** The doc is blunt about
why: no edge node has reliably delivered a reading yet (`A1:B0` still reports
`delivered=0`, unexplained), so changing the protocol now means debugging two
unknowns at once. It also flags the trap that would most likely introduce a
real vulnerability: `seq` is `uint16_t` and resets on cold boot (already
observed on the bench with powerbanks cutting out under deep-sleep current).
Harmless today — it only feeds de-dup — but feed it into a crypto nonce and
that reset becomes nonce reuse, which in AES-GCM leaks the authentication key
and lets an attacker forge readings the rules engine will act on.

`pytest pi/tests`: 193 passed.

---

## TL;DR of the previous session (2026-08-12 — plain-language UX overhaul)

The app talked to its user the way a debugger talks to its author. This pass
fixes that, for a grower who does not know what MQTT, a MAC address, dBm or a
mesh is. App-only: no firmware, no protocol changes. Planned first (see
`.claude/plans/`), audited with three parallel read-only agents, then every
finding verified in the source by hand before anything was written — several
of the agents' reported line numbers and one whole claim were wrong.

**Devices have names now.** New `services/device_names_store.dart` (mirrors
`NodePositionsStore` exactly: same SharedPreferences-single-JSON-blob shape,
same provider style) plus `utils/display_name.dart`, resolving user-set name →
zone (`zone1` → `Zone 1`) → last 4 of the MAC. Long-press to rename on the
device list and the switches list; on the mesh map long-press is already taken
by unpin, so renaming lives in the detail sheet. The MAC stays as a subtitle —
it is what you match against the sticker on the physical box.

**Engineering units became words.** `signalLabel`/`batteryLabel`/`sleepLabel`/
`connectionLabel` in `utils/plain_language.dart`: `-55 dBm` → "Strong",
`rank 2` → "Passes through Tomato bed (2 steps to the hub)", `sleepy` →
"Battery saver" (it reads like a fault; it is why the battery lasts). The
raw values are not gone — they moved into a collapsed "Technical details"
section in the node detail sheet, which is where you compare against
`mosquitto_sub` output on the bench.

**The thresholds are now shared, not copied.** `-60`/`-75` live in
`plain_language.dart` and are imported by `linkQualityOf`; `80`/`50`/`20`
likewise by `batteryIconFor`. Two tests assert the word and the colour/icon
change bands at exactly the same values — they fail if someone edits one
number and not the other, which is how a legend ends up disagreeing with the
line next to it.

**No raw exception reaches the user.** `utils/friendly_error.dart` maps
timeout / unreachable / certificate-mismatch / malformed-reply / unknown to a
title, an explanation and cheapest-first steps; `screens/common/
friendly_error_view.dart` renders that with `e.toString()` under an expander.
Ordering matters and is tested: `HandshakeException` is an `IOException`, so a
possible impersonation would otherwise be reported as a generic connection
failure.

**Two messages were actively misleading and are gone.** Discovery failure said
"Make sure you are on the same WiFi" — on the phone-hotspot topology the user
*is*, and mDNS still cannot work, because Android's `MulticastLock` governs the
client radio and not SoftAP. And the certificate-mismatch error led with
"someone may be impersonating your greenhouse" when the overwhelmingly likelier
cause is that the owner reinstalled the hub; it now leads with that and keeps
the real warning second.

**Safety.** "Disconnect" wiped the pairing on a single tap, one row below a
navigation item, with no undo (the only way back is the QR code). It now
confirms, and is called "Forget this greenhouse". And Settings no longer prints
`sudo bash scripts/selftest.sh` at the user — that is an operator instruction
and now lives only in `SECURITY.md`.

**Honesty.** The offline banner says *when* ("showing readings from 09:12"),
reusing `formatLastSeen`, via a new `lastSensorSightingProvider` that takes the
newest non-null `lastSeen` across nodes. When nothing has ever been confirmed
it says "no readings yet" rather than inventing a time — the same principle as
this morning's retained-`ts` work. The soil warning explains its own rule, and
`30` became `kLowSoilMoisturePct`, quoted in the text a test pins to it.

**Deliberately not done:** automatic sensor discovery. `TRUSTED_NODES[]`
(`mesh_config.h:124`) is a compile-time constant and `mesh_node.h:517` drops
any unlisted MAC; adding a node means editing that header and reflashing the
whole fleet, since ESP-NOW registers encrypted peers up front (max 8). It is a
firmware/provisioning problem, not a UI one, and firmware cannot be built here.
Also kept: every Advanced pairing field — they are the only path when discovery
fails — but each now has a hint saying what it is and where to find it.

`flutter test`: 279 passed (was 213). `flutter analyze`: clean. Built and
installed on the bench phone.

---

## TL;DR of the previous session (2026-08-12 — real "last seen", drag-to-pin fix, QR tool)

Driven entirely by the owner using the app on the bench and reporting what was
wrong. Three separate real bugs, each found by reproducing the complaint rather
than trusting the previous session's notes.

**"Last seen" was the previous session's fix done badly, and this is the real
one.** 2026-08-11 stopped `lastSeen` advancing on an offline `/status`, which
was correct but incomplete: MQTT replays *every* retained topic in full on
every (re)connect with nothing in the protocol saying how old a message is, so
every node still read as "seen just now" the instant the app opened. That
session documented the gap and moved on; this one closes it properly, at the
source rather than in the UI:

- `serial_bridge.py` now stamps `ts` (epoch seconds) into every `/mesh` payload
  it publishes. Because the topic is retained, **the stamp of the last real
  sighting is what survives on the broker** — exactly the value "last seen"
  wants, and it stays true through a replay.
- The MQTT `retain` flag is now threaded from `mqtt_connection.dart` through
  `NodeStatus` into the repository merge, so an event that is *only* a replay
  can no longer be mistaken for evidence of life.
- `NodeStatus.lastSeen` became nullable: "never confirmed alive" is a real
  state, distinct from a time. It renders as `Unknown` rather than a
  fabricated one. The merge takes the *later* of the two known times, since
  retained topics replay in arbitrary order and last-seen must be monotonic.
- Found and fixed a gap in the above while bench-testing it: the bridge's own
  root record is sent by firmware only from `setup()`, so a `serial_bridge`
  restart while the ESP32 keeps running would never see one and never refresh.
  It now synthesises the canonical record (`parent: null, rank: 0` — the
  firmware's own definition of itself) as a fallback.

Verified on the bench Pi: the bridge's retained record now carries a live `ts`.
The two edge nodes still read `Unknown` and that is correct — they have not
reported since the upgrade, so the Pi genuinely never recorded when they were
last seen. Inventing a time there would be the bug, not the fix.

**Drag-to-pin on the mesh map silently did nothing, and "Unpin" looked broken
as a result.** `_buildCard` switched between `Positioned` and
`AnimatedPositioned` — *different widget types* — the moment a drag began, with
no `Key` on either. `Element.canUpdate` returns false on a type change, so
Flutter tore down and rebuilt the subtree, destroying the live
`GestureRecognizer` mid-gesture. `onPanStart` fired once (recording the
*pre-drag* position), and `onPanUpdate`/`onPanEnd` for that touch never
arrived — so the "pin" captured exactly where the card already was,
indistinguishable from auto-layout, leaving Unpin with nothing visible to
undo. Fixed with a stable `ValueKey(nodeId)` and one unchanging widget type
(animate via `duration: Duration.zero` instead of switching classes).
Confirmed by a failing-first widget test driving a real multi-step gesture.

**`pi/tools/show_qr.py` was silently stale twice over.** It took credentials as
CLI args and recomputed a TLS fingerprint with `openssl` against a single cert
file, so it never gained `api_token`/`cam_token` after the 2026-07-28 hardening
pass, and produced one fingerprint where `device.json` stores the
comma-separated CA+leaf pair (`79e37ff`). A QR from it paired "successfully"
with an empty token — reproducing the very false-alert bug fixed the day
before — and a fingerprint pinning would never match. Rewritten to read the
exact files `portal.py`'s `_pairing_payload()` reads, so the two cannot drift
again, plus LAN-IP auto-detection (the Pi's IP changes per DHCP lease on the
phone-hotspot topology). New `pi/tests/test_show_qr.py` asserts the payload
matches `ConnectionConfig.fromJson`'s field set.

**Ghost nodes cleared off the bench broker.** `zone1_test` and two retired MACs
(`75EC`, `6B50`, `88F1…`) were still sitting in retained topics from old
hardware, showing up as phantom devices and a phantom dashboard zone. Retained
messages live forever until explicitly cleared; six topics published empty.
The broker now matches `mesh_config.h` exactly: bridge + zone1 + zone2.

`pytest pi/tests`: 193 passed. `flutter test`: 213 passed. `flutter analyze`
clean.

**Process note worth keeping:** `flutter install` does **not** build — it
installs whatever APK already sits in `build/`. Used alone it reinstalled a
15-day-old binary that still contained the parked camera screen, which read as
"the fixes didn't work". Always `flutter build apk --release` first.

---

## TL;DR of the previous session (2026-08-11 — history false-alert fix, mesh map bugfixes, overview doc)

Follow-up work later the same day as the credential scrub below, triggered by
the owner actually using the app and noticing (1) a "security alert" pushed
to their own phone every time they opened weather history, and (2) the mesh
map "feels buggy" — nodes shown offline with a last-seen time that reads as
"just now".

**History screen was crying wolf at its own owner.** A phone paired before
2026-07-28 has no `api_token` stored locally (the field didn't exist yet at
pairing time). Every history-screen open sent `/api/history` with no auth,
`portal.py` correctly 401'd, and `history_auth_failure` is one of
`security_log.py`'s alertable kinds — so the owner's own stale pairing
triggered real "Greenhouse security alert" pushes indistinguishable from an
actual intrusion attempt. Fixed client-side: `historyPointsProvider` now
checks `config.apiToken.isEmpty` and throws a dedicated
`HistoryTokenMissingException` *before* ever hitting the network, so the
doomed request (and the false alert it caused) never happens; the history
screen shows a "Re-pair" button instead of a raw 401. Owner still needs to
re-pair once to get a real token.

**Mesh map: the actual bug behind "shows offline but last-seen says now".**
`greenhouse_repository.dart`'s `/status` merge wrote `lastSeen: event.lastSeen`
unconditionally, and `event.lastSeen` is stamped `DateTime.now()` at parse
time regardless of what the payload says — an offline event (which carries no
timestamp of its own; the bridge/serial_bridge just publish the bare string
the instant they detect staleness) was overwriting a node's last-seen with
the exact moment it was found to be gone. Fixed: lastSeen now only advances on
a transition *to* online. Consequence fix: `NodeListTile` and the mesh map's
detail sheet now qualify last-seen with a date once it isn't today
(`app/lib/utils/last_seen_format.dart`) — a bare `HH:mm` misreads as "recent"
once nodes can legitimately show hours- or days-old timestamps. Known,
documented-not-fixed residual gap: a *retained* MQTT redelivery (e.g. on app
reconnect) still advances lastSeen, since the wire protocol carries no real
timestamp to fall back on either — fixing that needs the MQTT retain flag
threaded through `mqtt_connection.dart`, out of scope for this pass.

**Mesh map: "how do I know if a node is bridged" now has an answer on
screen.** `meshRank` already *is* hop-count-from-bridge in this mesh (one
bridge, rank assigned by hop distance) but nothing surfaced that — added an
explicit "Direct"/"N hops" label on each card and a "Connection" row in the
detail sheet ("Direct to bridge" / "Relayed — 2 hops (via node1)" / "This is
the bridge" / "Unknown — no mesh data yet"). Also added a link-quality color
legend and a node-count summary bar (`N nodes · N online · N offline`) —
both were flagged as "missing structure", and the map had zero explanation
of what its own link colors meant.

**New doc:** `docs/SYSTEM_OVERVIEW_SIMPLE.md` — a friendlier, prose-style
walkthrough combining the full system layout and the security work, meant as
the first thing to read before `ARCHITECTURE.md`/`docs/technical/`.

`flutter test`: 200 passed (was 184; ~15 new tests for the token-missing
path, the lastSeen-on-offline fix, the last-seen date formatter, and the
Direct/hop-count labels). `flutter analyze`: clean.

**Rebuilt and reinstalled the app on the test phone** to pick up both fixes.
`flutter install` uninstalled the previous build first (different keystore
on this machine than whatever built it before — known gotcha, see the
"App on the phone" section further down) — all local app data including the
old pairing was wiped, so this was effectively a from-scratch re-pair, not
just a token refresh.

**Re-pairing surfaced a live topology problem: the phone is on its own
hotspot with the Pi as a client, and mDNS "Find my greenhouse" cannot work
there.** Android's `WifiManager.MulticastLock` (which the app already
acquires around its mDNS lookup) only governs the WiFi *client* radio; when
the phone is itself the hotspot, its radio is in SoftAP mode, a state the
app has no hook into at all. This isn't new (matches the 2026-08-10 bench
notes) but is worth restating: on this topology, only manual entry or QR
scan can pair, never the search button.

**That surfaced `pi/tools/show_qr.py` was stale in exactly the same way as
the history bug above.** It never gained `api_token`/`cam_token` after
2026-07-28, and separately computed a single TLS fingerprint via `openssl`
instead of reading the comma-separated CA+leaf pair `device.json` actually
stores (`79e37ff`) — so a QR from it would pair "successfully" with an empty
token (the same false-alert bug) and a fingerprint pinning would never match.
Rewritten to read the exact files `portal.py`'s `_pairing_payload()` reads,
so the two can't drift again; also auto-detects the Pi's current LAN IP
(routing-table trick, sends no packet) instead of requiring a manually-typed
`--lan`, since that IP changes every DHCP lease on the hotspot topology.
`pi/tests/test_show_qr.py` (5 tests, new) checks the payload shape.
`pytest pi/tests`: 187 passed (was 182).

Full detail and rationale: `IMPROVEMENTS.md` Β8-Β11.

---

## TL;DR of this session (2026-08-11 — git-history credential scrub)

Ran the scrub that `SECURITY.md` §1 Step 3 had been describing as optional
since 2026-07-28. `git filter-repo --replace-text` rewrote all 223 commits.

**What came out:** two WiFi passwords (home router + phone hotspot), two SSIDs,
the 20-char MQTT password, and an early throwaway `#define MQTT_PASS "123"`.
Verified: the five distinctive literals now return **0 hits** across every
commit, against 2057 in the pre-scrub mirror kept as a backup.

**The `"123"` one needed a `regex:` rule, not a literal one.** As a literal
replacement it would have matched the substring `123` in 15 unrelated files —
`pubspec.lock` version numbers, `123456` test PINs, `portal.py`. Anchoring it
to `#define MQTT_PASS\s+"123"` scrubs the credential and leaves the rest alone.

**Docs that had gone stale the moment the hashes changed**, now corrected:
`SECURITY.md` §1 (was telling you to `git show c0383b3`, which no longer
resolves), `check_leaked_secrets.py`'s leak descriptions, `rotate_secrets.sh`'s
header comment, and `10-security.md` §8.7 (still claimed the secrets were in
history).

**`check_leaked_secrets.py` gained two hashes** — the hotspot WiFi password and
the `"123"` MQTT password were leaked too but were never on its list, so a unit
still using either passed the healthcheck clean. Now it fails, as it should.

**Rotation is still not done, and the scrub did not do it.** Anyone who cloned
before today still holds working credentials; the router and HiveMQ passwords
in particular live outside anything this repo can reach. `SECURITY.md` §1
Steps 1–2 remain open.

`pytest pi/tests`: 182 passed.

---

## TL;DR of the previous session (2026-08-10 — WiFi provisioning + UART bridge fixed, full bench deploy)

Bench session on a real Pi Zero W + ESP32-C3 bridge + phone hotspot. Commits
`3ce691c`..`4f190ab` on `main`. Full details/rationale are in those commit
messages and `TODO.md` — this is the map of what changed and why it matters.

**C3 soil sensor was reading dead air.** `SOIL_DATA_PIN` was GPIO2, an
ESP32-C3 strapping pin some boards carry a hardware pull-up on — `analogRead()`
sat at 4095 permanently, indistinguishable from a cold solder joint. Moved to
GPIO1; readings now track moisture correctly. **Every already-wired C3 node
needs the AOUT wire moved + a reflash.** Added `firmware/sensor_pin_test/`, a
standalone bench sketch for checking joints without the mesh stack in the way.

**WiFi first-time setup was unusable — four stacked bugs, each masking the
next:** `/etc/greenhouse` was root-owned while the portal runs as `pi` (500 on
submit); `_reboot_soon()`'s `bash -c "sleep 3 && sudo reboot"` silently never
reached `sudo`; the typed SSID was written and committed with zero check it
was even visible (NetworkManager matches byte-for-byte — `billyredmi` typed
for a hotspot actually named `REDACTED_WIFI_SSID` cost most of a session before this
was fixed); and the watchdog reverted to AP mode recording nothing about why.
Now: `/connect` rejects an unreachable SSID and suggests the closest visible
name; the watchdog writes `wifi_fail.log` to the boot partition before
reverting, pairing the configured SSID byte-exact against what's actually
visible.

**UART bridge to the Pi: resolved, not actually broken.** The 2026-07-27
"zero bytes on `/dev/serial0`" turned out to be a diagnostic artifact — `head
-c N` buffers and gets killed by `timeout` before flushing, reading as a dead
link. An instrumented pyserial read shows the bridge heartbeating reliably
(11 lines/20s @ 115200). `dtoverlay=disable-bt` is **not** needed here despite
being the usual fix for this symptom — `enable_uart=1` already pins core_freq
on this Zero W.

**Not yet proven: any edge node delivering a reading.** The UART carries
`heartbeat` lines only; zero `reading` lines seen; no mesh rows reached the
recorder. `zone1`/`zone2` values visible in MQTT during this session were
*retained* messages from an earlier session, not live data — easy to mistake
for a working feed since retained topics read back instantly on subscribe.

**`serial_bridge.py` stale-status bug:** liveness tracking only started once a
heartbeat had been seen, so restarting the service while the ESP32 was
unpowered left every retained `online` standing forever. Now sweeps once at
startup and retracts stale claims if no heartbeat arrives within the timeout
window. Found two paho-mqtt ordering bugs writing the fix (callbacks must be
registered before `connect()`; `SUBSCRIBE` must be issued from `on_connect`,
not inline) — both made the sweep silently never run in the service even
though identical code worked driven by hand.

**Full `deploy.ps1` run against the bench unit**, which surfaced one more real
bug: `gen_certs.sh`'s mosquitto-cert idempotency guard sat in front of the
portal HTTPS keypair copy, so any unit whose mosquitto cert predated that copy
step could never get a portal keypair from any later install. Split into two
independent checks. `selftest.sh`: **45 passed, 0 failed** (was 29/37 before
this session). `selftest.sh` also gained a UART-bridge section — it never
checked `greenhouse-serial-bridge` before, only the unrelated
`greenhouse-hivemq-bridge`, so a dead sensor link could pass clean.

**App rebuilt and reinstalled from a second dev machine wiped local pairing
data** (full uninstall+reinstall, not an in-place update) — see the ⚠️ note
under Quick Start below for why and what to expect. Re-paired manually since
mDNS discovery doesn't work on a phone-hotspot topology (Android's
`MulticastLock` covers the client radio, not the SoftAP interface a hotspot's
own clients arrive on).

**Deliberately NOT done this session:** rotating the `app` MQTT password,
still `123` on this unit — `install.sh` only backfills *missing* `device.json`
fields, never regenerates existing ones, and rotating would break the
just-re-paired app. Use `rotate_secrets.sh` when that's wanted.

---

## TL;DR of this session (2026-08-02 — camera parked, dead weight removed)

The camera never worked well enough on real hardware to keep carrying, so it
was **parked, not deleted**: every camera file was `git mv`'d under
`parked/camera/` (history intact, `git log --follow` works), and everything
that referenced it was unwired.

- **App:** no Camera tab or `/camera` route; `cam/*` MQTT topic routing,
  the repository's cam streams / `fetchEventPhoto` / live-frame reassembly,
  `CamStatusRaw`/`CamEventChunkRaw`/`CamLiveFrameChunkRaw`, the
  `motion_alert` notification switch and the `'motion'` alert title are all
  gone from `app/lib`. Dependency `flutter_mjpeg` dropped.
- **Pi:** `install.sh` no longer installs `python3-pil`, provisions
  `cam_token.txt`, or copies/enables/restarts `greenhouse-cam-bridge` — and
  it now actively disables+removes that unit on units that already have it,
  so a redeploy leaves nothing running. `weather.py` no longer carries
  `motion_alert`.
- **CI:** `Pillow` dropped from the pip install line (only the parked
  motion tests needed it).
- **Docs:** technical docs, `DEVICES.md`, `ARCHITECTURE.md`, `TODO.md` and
  `IMPROVEMENTS.md` now mark the camera as parked rather than live. The
  design specs/plans under `docs/superpowers/` were left alone — they're
  history.

Net: −359 lines from the live tree, two dependencies gone, one fewer
always-on service on the Pi. Restore instructions (including the exact
shared-file edits to undo) are in `parked/camera/README.md`.

**Pi tests:** 115 passed. The 2 `test_push.py` failures seen locally are the
known `firebase-admin`-not-installed-in-sandbox case, not a regression — CI
installs it. Flutter isn't available in that sandbox, so `flutter analyze`
/ `flutter test` were verified by CI, not locally.

---

## TL;DR of this session (2026-07-28 — security hardening pass)

**Merge note (2026-08-07):** this pass predates the camera parking above.
Everything below that touches the camera — the `POST /cam/frame` gate, frame
signing, frame encryption, `/stream` token auth, the `CAM_TOKEN` provisioning
— was real work and its files moved intact under `parked/camera/`, but none
of it runs today: the cam bridge is not installed and `install.sh` removes it.
Treat those items as fixes banked against a future restore. The non-camera
hardening (portal HTTPS + auth, per-IP lockout, systemd sandboxing, ACL fix,
SSH hardening, secret rotation, leak detection) is live.

Full security review of every attack surface, then fixes. **One critical
vulnerability found and closed**, plus four smaller real issues. 30 new tests
(179 pytest / 187 Flutter, all green; `flutter analyze` clean).

**Critical — `POST /cam/frame` was completely unauthenticated.** `cam_bridge.py`
listens on `0.0.0.0:8090` and accepted a snapshot from anyone on the LAN. The
chain: `_update_heartbeat()` sets `_camera_ip` from the *sender's* address, so
one POST made an attacker "the camera"; every subsequent Pi fetch
(`/capture`, `/event/<id>`) then went to that attacker **carrying
`?token=CAM_TOKEN`** — handing over the token that authorizes
`DELETE /event/<id>`, i.e. the ability to wipe stored motion evidence off the
real camera's SD card. It also let the attacker feed arbitrary images to the
app as live/event frames and trigger unlimited motion alerts / push
notifications / DB rows. Fixed with a token gate (`X-Cam-Token` header, or
`?token=` for bench debugging), `hmac.compare_digest`, fail-closed when
unprovisioned, plus a body-size cap. 7 tests, **verified to fail** with the
gate removed.

**Also fixed:**
- `/api/history*` served the unit's entire sensor history to anything on the
  LAN/hotspot. Now bearer-token gated via a new per-unit `api_token`.
  `install.sh` backfills it onto already-provisioned units — without that,
  `first_boot.sh`'s `.provisioned` sentinel would have left existing Pis
  permanently 401ing.
- Camera `/stream` was the last open endpoint (`IMPROVEMENTS.md §Α5`'s
  remainder). Built the full cross-stack chain: portal payload →
  `ConnectionConfig.camToken` → `streamUrl()` → firmware `checkCamToken()`.
- PIN comparison used `!=`, which short-circuits and leaks the correct prefix
  through response timing. Now `hmac.compare_digest`.
- Mosquitto ACL allowed the `bridge` user 4 topics but the firmware publishes
  6 — `/battery` and `/mesh` were being **silently denied** since the
  2026-07-26 telemetry work. Security-config bug and a functional one.

**Hardening:** full systemd sandbox set across all services (two documented
exceptions: serial-bridge keeps `/dev` access, portal keeps setuid-sudo
compatibility); portal gained CSP with a per-request nonce, security headers,
a 64KB body cap, and no longer echoes exception text to clients; `selftest.sh`
gained security regression checks that assert the gates return 401.

**Second wave (same session) — the gaps the first pass had only documented:**
- **TLS on the LAN links.** The portal now serves **HTTPS on 8443** using the
  same per-unit cert as the broker, and the app pins the same fingerprint it
  already pins for MQTT (new shared `app/lib/utils/cert_pinning.dart`; the
  MQTT path was refactored onto it so the two can't drift). Port 80 stays
  plaintext *only* for the captive portal, which can't work over TLS. Verified
  end-to-end against a real TLS server: authenticated 200, unauthenticated 401,
  enforcement flag 403s plaintext while leaving the captive portal reachable.
  An opt-in `/etc/greenhouse/require_https` closes the downgrade path once
  every paired phone is updated (not default — it would lock out old builds).
- **Global pairing lockout was itself a DoS.** 5 wrong PINs from *anyone*
  latched a global flag until service restart, so any device on the network
  could permanently block the owner from pairing. Now per-IP with automatic
  expiry, a bounded state table, a much higher global backstop, and
  `X-Forwarded-For` deliberately not trusted.
- **`CAM_TOKEN` default was a value published in this repo.** Now generated
  randomly per unit in `first_boot.sh` (so clones differ), auto-replaced on
  units still holding the placeholder, and `selftest.sh` fails if it sees it.
- **Shared fleet admin SSH key** is no longer baked into shipped clones
  (`prep_image.sh` strips it; `KEEP_ADMIN_KEY=1` retains it deliberately), and
  SSH password auth is disabled — guarded, so a unit with no authorized key
  keeps password login rather than locking the owner out, with `sshd -t`
  validation and rollback.
- Rate limiting on the camera frame intake, checked before the token compare.

**Third wave — closing what the second wave had called structural:**
- **First-contact MITM is now closed for the QR path.** Pairing pins the
  certificate whenever a fingerprint is known out of band (QR scan or manual
  entry), rejecting a mismatched cert *before* the PIN is sent, with an
  explicit security warning in the UI. It never silently downgrades to
  plaintext once a fingerprint is known. Only the mDNS-without-QR path still
  trusts on first use — and a 6-digit PIN provably cannot fix that (any
  proof-of-knowledge is offline brute-forceable from one captured exchange),
  so the pairing screen now recommends QR as the secure path.
- **`CAM_TOKEN` no longer crosses the wire.** Frame POSTs are HMAC-signed —
  `HMAC-SHA256(token, timestamp + sha256(body))` — so a passive sniffer sees
  only a signature bound to one body and one moment. Replays are rejected. I
  transcribed the firmware's signing independently and verified the Pi accepts
  it, and that tampered bodies, wrong tokens, stale timestamps, malformed
  timestamps, and replays are all refused. `no_unsigned_cam` drops the legacy
  bearer path once every camera is reflashed.
- **Credential rotation is now one command.** `pi/scripts/rotate_secrets.sh`
  rotates every Pi-owned secret (both MQTT passwords, API token, PIN, cam
  token, and the TLS keypair) and prints exactly what must be re-paired and
  re-flashed. New `SECURITY.md` carries the full checklist including the
  external steps (router, HiveMQ) and the git-history scrub procedure.
- Confirmed the leak is real and specific rather than assumed: commit
  `c0383b3` carries a live WiFi password and MQTT password. `SECURITY.md`
  names them so the rotation can be verified as complete.

**Fourth wave — the last three code-fixable items:**
- **Camera frames are now encrypted**, closing the final plaintext data path.
  AES-256-GCM, key = `HMAC(CAM_TOKEN, "greenhouse-cam-frame-key")`, wire format
  `nonce||ciphertext||tag`. No new dependency: `cryptography` already arrives
  with `firebase-admin`, and the import is guarded so a unit without it rejects
  encrypted frames rather than crashing. I transcribed the firmware's
  `encryptFrame()` byte-for-byte and confirmed the Pi decrypts it, that the key
  derivations match, and that the plaintext JPEG never appears in the wire
  bytes. `require_encrypted_cam` refuses plaintext once cameras are reflashed.
- **Safety code** (`ABCD-EF12`, derived from the pinned fingerprint) is printed
  by `selftest.sh` and shown in the app under Settings. This is what makes an
  active MITM on the mDNS pairing path *detectable* — its substituted
  certificate yields a different code. Both derivations are cross-checked by a
  test that pins the exact expected value, so they can't drift apart.
- **`check_leaked_secrets.py`** fails the healthcheck while any credential
  published in git history (verified: `c0383b3`) or any public repo placeholder
  is still live — so "did I actually rotate?" is answered by the system, not by
  memory. 8 tests.

**Explicitly still open** — see `SECURITY.md §3`:
1. **Rotating the router WiFi and HiveMQ Cloud passwords** — everything the Pi
   owns is now one command (`rotate_secrets.sh`); these two live in systems
   this repo cannot reach.
2. **Active MITM on a first mDNS pairing is detectable, not prevented** —
   compare the safety code, or pair via QR (which *is* prevented). Genuinely
   unfixable with a 6-digit PIN without a PAKE.
3. **Shared ESP-NOW keys** — per-node keys need provisioning the project
   doesn't have, and per-pair derivation from a shared PMK buys nothing.
   Deliberately not attempted blind: changing mesh crypto with no compiler or
   hardware risks silently breaking fleet mesh join, the exact failure mode
   this repo's docs call hardest to diagnose remotely.
4. **Single-tenant HiveMQ and unencrypted SD card** — the first needs
   per-customer cloud infrastructure; the second has no good answer on headless
   Pi Zero W hardware with no TPM.

Everything in `firmware/` still carries the standing caveat that nothing here
has been compiled or flashed from this sandbox — the camera's new signing code
in particular needs a bench run.

---

## Next step

> **Superseded by 2026-08-10 (see that TL;DR above):** item 1 below is
> resolved — the bridge was never broken, "zero bytes" was a diagnostic
> artifact of `head -c N` getting killed by `timeout` before flushing. Item 2
> is moot — the camera was parked on 2026-08-02, after this list was written.
> Current next step is proving an edge node delivers a real `reading` over the
> now-working UART link, per that TL;DR's "not yet proven" note.

**Immediate — from the later 2026-07-27 session:**

1. ~~**Bridge UART: confirm reflash status.** Wiring + power are confirmed
   correct (see that session's TL;DR), but `/dev/serial0` shows zero bytes.
   Check whether `bridge_esp32.ino` (current UART version) has actually been
   flashed onto the board yet — if it's still running the old WiFi/MQTT
   firmware, that fully explains zero UART output. If reflashing doesn't fix
   it, `firmware/bridge_esp32_wifi_fallback/` is ready to flash instead (WiFi
   + MQTT direct to the Pi, no GPIO wiring/raspi-config step needed) — a real
   `bridge` MQTT account already exists on the Pi for it.~~
2. ~~**ESP32-CAM: flash and bench-test.** Firmware now compiles (missing
   `secrets.h` and a missing `UriBraces` include were both real blockers,
   now fixed) — this hasn't been flashed/tested yet. Watch the serial
   monitor at 115200 baud for camera/SD init, WiFi connect (`REDACTED_WIFI_SSID`),
   and the snapshot-POST cadence reaching `cam_bridge.py`.~~
3. **Mesh relay + deep-sleep Phase 1** — per
   `docs/superpowers/plans/2026-07-26-mesh-deep-sleep.md` Task 6 and the
   original relay plan's Task 5. Untouched this session.
4. Once all of the above are confirmed working end-to-end, this repo will
   finally have its firmware fully field-validated, not just code-reviewed.

**Also worth doing:** the hardware bring-up edits stashed at the top of this
session's TL;DR (`git stash list`) have real learned values (device MACs,
GPIO pin fixes, soil calibration) that were never reapplied against the
newer UART-based firmware — worth a deliberate pass rather than leaving
them stashed indefinitely.

**After that**, other candidates (see `TODO.md` for the full picture):

1. **Phase 2 mesh deep sleep** (synchronized wake windows so relay-capable
   nodes can sleep too) — deliberately deferred until Phase 1's bench run
   produces real per-board RTC-drift measurements; see the deep-sleep
   spec's §Phase 2.
2. **Direct-to-Pi pairing + PIN auth** (`docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md`)
   — approved in conversation, no implementation plan written yet.
3. Work through `IMPROVEMENTS.md`'s remaining open findings — Β3 (LAN
   streaming blocks motion detection) is directly relevant to the cam bench
   work above; Α1's rotation step (WiFi/HiveMQ credentials) is still a live
   real exposure whenever convenient, and is now smaller in scope than
   before — the bridge no longer has WiFi/MQTT credentials to rotate at
   all after this session's change (only the cam's WiFi credentials and
   the Pi's own HiveMQ credentials remain).

Ask the user which before picking one — none was prioritized explicitly
beyond the immediate cam debugging already underway.

---

## TL;DR of this session (2026-07-27, later — login bugs + bench bring-up)

Started as "pull latest, deploy the Pi, rebuild the app" and became two real
bug hunts plus hardware bring-up, after the user (rightly) pushed back hard
on "check your input" guesses that turned out wrong — see
[[feedback_verify_dont_blame_user]] in memory for the process lesson.

**Repo sync:** local `main` was 51 commits behind `origin/main` with
uncommitted hardware bring-up edits (real MACs, WiFi creds, GPIO pin fixes,
soil calibration) that conflicted with the newer UART-bridge rewrite.
Rebased the one genuine local fix (cam-bridge restart-on-redeploy) onto the
pulled history and stashed the rest rather than guess-merging hardware
values into now-restructured files — that stash (`stash@{0}` as of this
writing) still has those edits if anyone wants to reapply them deliberately.

**Bug 1 — permanent black screen on reinstall:** `PairingService.loadConfig()`
let a `BadPaddingException` (stale Keystore-encrypted blob after a reinstall)
escape into go_router's redirect, leaving the router with no route to
resolve. Fixed with try/catch + `resetOnError: true`; 5 new tests.

**Bug 2 — the real one, took most of the session:** every single TLS/MQTT
login attempt failed with a generic "Could not connect", regardless of how
correct the address/password/fingerprint were. Traced (after wrongly
suggesting typos/autocorrect multiple times — the thing the user was angry
about) to an unsound internal cast in `mqtt_client` 10.11.11:
`onBadCertificate as bool Function(Object)?` throws a `TypeError` for any
real `X509Certificate`-typed callback, before a single cert is inspected.
Broken since TLS pinning was added (PR #12); silently swallowed by
`_tryConnect`'s catch. Fixed by typing the app's callback parameter as
`Object` instead (contravariance still satisfies the field's declared
type; runtime type now matches what the internal cast expects). Verified
end-to-end against the real Pi: `CONNECTED SUCCESSFULLY` /
`connectionAccepted`. Also fixed underneath: the Pi's self-signed chain
triggers `onBadCertificate` twice (CA, then leaf — the leaf has no SAN, so
connecting by IP is a separate hostname-mismatch failure), so
`tls_fingerprint` is now a comma-separated list of both, generated by
`first_boot.sh`.

**mDNS ("Find my greenhouse"):** added a real `WifiManager.MulticastLock`
via a new Android platform channel (`MainActivity.kt` +
`multicast_lock.dart`), confirmed via logcat that it acquires/releases
correctly — and discovery *still* doesn't find the Pi. Reason: the bench
network is the phone's own WiFi hotspot, not a router, and `MulticastLock`
governs the WiFi **client** radio's multicast filter, not the SoftAP
interface a hotspot's own clients arrive on. The fix is correct and will
help on a shared router; it doesn't and can't fix this specific topology.
Manual IP entry is the reliable path while on this hotspot.

**Hardware bring-up:**
- **Bridge (UART):** wiring + power confirmed correct against
  `INSTRUCTIONS.md` Part 6 (GPIO4→pin10/RXD, GPIO5←pin8/TXD, GND→pin6,
  5V→pin2/4, board's LED lit) — but `/dev/serial0` shows zero bytes across
  repeated samples. Not yet confirmed whether the board has actually been
  reflashed with the current `bridge_esp32.ino` since the UART rewrite;
  that's the leading suspect and the next thing to check.
- **Temporary WiFi fallback added:** `firmware/bridge_esp32_wifi_fallback/`
  — the last pre-UART bridge firmware, restored from git history and
  updated to the current network/credentials, so the user can get sensor
  data flowing again without the UART link while that gets sorted. Created
  a real `bridge` MQTT account on the Pi for it (didn't exist —
  `device.json` predated that field).
- **Camera:** never connected all session (`greenhouse/cam/status` stuck at
  `online: false`) — root cause was `secrets.h` never existing, so the
  current firmware couldn't even compile. Created it (gitignored,
  per-device), added the missing Arduino-library junction (same pattern as
  `GreenhouseMesh`), set a real `CAM_TOKEN` on the Pi (was still the
  install-time placeholder), and fixed two real compile bugs found while
  actually trying to flash it: missing `#include <uri/UriBraces.h>>`, and
  `PI_HOST` switched from `greenhouse.local` to a direct IP (`HTTPClient`
  doesn't resolve mDNS reliably on this core — same class of bug as the
  mqtt_client fix above). Compiles now; not yet flashed/bench-tested.

**Also added:** `docs/DEVICES.md` (real device MAC registry + Arduino IDE
setup notes, committed — was sitting untracked from an earlier local
session), `firmware/fake_edge_node_esp32_c3/` (random-walk fake sensor node
for bench-testing the pipeline without real sensors), `install_to_pi.py`
(cross-platform paramiko/scp alternative to `deploy.ps1`).

**Deployed:** Pi redeployed via `deploy.ps1` after the repo sync (28/28 real
checks, 2 transient restart-timing failures — normal). App rebuilt and
installed multiple times across the session; last install has all fixes
above. Two confirmed-stale remote branches deleted after verifying their
content was fully superseded on `main`.

---

## Older sessions

Session TL;DRs from 2026-07-08 through 2026-07-27 (UART-wired bridge
implementation, mesh deep sleep + field visualization, the ESP32-CAM's
first design pass, the technical-docs/CI session, FCM push + alert rules,
and earlier) were moved to `docs/archive/HANDOFF_ARCHIVE.md` on 2026-08-07
to keep this file focused on current state. That archive is historical —
`TODO.md` and `IMPROVEMENTS.md` are the maintained, code-verified backlog.

---

## Quick Start (next session)

```bash
# SSH to the master Pi — passwordless (admin key authorized)
ssh pi@greenhouse.local
# DHCP IP varies by session; OS password is per-unit random (see
# /boot/firmware/INITIAL_PASSWORD.txt) — use the SSH key, not a password.

# Verify the Pi
ssh pi@greenhouse.local "sudo bash /home/pi/greenhouse/scripts/selftest.sh"
# Expect 45/45 (as of 2026-08-10, real bench unit). If "portal not responding"
# or "history endpoint NOT protected (got 000)" shows up, it's a startup race
# — the portal takes ~6-8s to rebind port 80 on a Zero W after any restart.
# Rerun a few seconds later before treating either as a real failure.

# Reopen the pairing window (it auto-expires after 600s uptime)
ssh pi@greenhouse.local "sudo systemctl restart greenhouse-portal"

# Redeploy the Pi side after code changes — from repo root, NOT manual scp:
.\deploy.ps1                          # defaults to greenhouse.local
.\deploy.ps1 -PiHost 192.168.1.54     # or target a specific IP

# No real sensors attached? greenhouse-simulator.service is installed
# (disabled by default) — enable it on demo/no-real-sensor units instead of
# the old ad-hoc systemd-run one-liner (IMPROVEMENTS.md Δ4):
ssh pi@greenhouse.local "sudo systemctl enable --now greenhouse-simulator"

# Build + install the app (phone via USB)
export PATH="$PATH:/c/Users/billy/flutter/bin"   # Git Bash
cd app
flutter pub get
flutter build apk --release
flutter install -d <device-id>   # `flutter devices` to list; approve the
                                  # install prompt ON THE PHONE when it appears
```

> ⚠️ **`flutter install` may silently WIPE the phone's local app data**
> (pairing state, secure storage, cached prefs) even though the release build
> type signs with the same `debug` signing config as debug builds
> (`android/app/build.gradle:39`). This happens whenever the installed APK's
> *actual* debug keystore doesn't match this machine's `~/.android/debug.keystore`
> — e.g. the previous install came from a different dev machine. Android then
> refuses an in-place update (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`) and Flutter
> silently falls back to uninstall-then-install. Tell: `flutter install` prints
> "Uninstalling old version..." instead of updating in place, and
> `adb shell dumpsys package <pkg> | grep firstInstallTime` matches
> `lastUpdateTime` exactly (bench-confirmed 2026-08-10). No known way to avoid
> this short of sharing one debug keystore across every dev machine — just
> expect a full re-pair after installing from a machine that hasn't built this
> app before.

---

## Architecture (current)

```
┌─────────── FIELD / GREENHOUSE ───────────┐
│  ESP-NOW sensor nodes, dynamic multi-hop  │  (mesh relay + deep-sleep
│    mesh relay (+ Phase 1 deep sleep for   │   phase 1 + UART bridge
│    battery nodes) → ESP32-C3 bridge       │   firmware all done, not
└──────────────────┬─────────────────────────┘   yet field-tested on real
                    │ UART GPIO, 3.3V, no router     HW — see this session's
                    │ (replaces WiFi/MQTT/TLS,        specs + docs/MESH_
                    │  2026-07-27)                    RELAY_EXPLAINED.md)
┌───────────────────▼───────────────────────────────┐
│  Raspberry Pi Zero W                              │
│  ├─ greenhouse-serial-bridge — UART→loopback MQTT  │
│  ├─ Mosquitto — local TLS 8883 + HiveMQ Cloud bridge│
│  ├─ greenhouse-recorder — SQLite history (minute   │
│  │    buckets → hourly rollup, 90d/2yr retention)  │
│  ├─ greenhouse-weather — Open-Meteo + automation   │
│  │    rules + forecast publish                     │
│  └─ greenhouse-portal — Flask :80, WiFi setup +    │
│       /pair + /api/history[/series]                │
└───────────────────┬───────────────────────────────┘
                    │ LAN (port 8883/80) or HiveMQ Cloud bridge (remote)
┌───────────────────▼───────────────────────────────┐
│  Flutter app (Android; iOS untested)              │
│  Dashboard, Control, Devices (+ Mesh Map screen,   │
│  2026-07-26), Weather+Rules, History, Settings     │
└─────────────────────────────────────────────────────┘
```

Remote access is **HiveMQ Cloud**, not Tailscale (dropped that plan entirely). No InfluxDB/Node-RED/Grafana — replaced by the lighter local SQLite recorder + in-app automation rules + push notifications, since the Pi Zero W can't comfortably run heavier services.

---

## Key files

| File | Role |
|---|---|
| `pi/install.sh` | Master installer (idempotent): packages, TLS, Mosquitto, all 6 systemd services |
| `pi/scripts/recorder.py` | Sensor history recorder — MQTT ingest → SQLite minute buckets → hourly rollup |
| `pi/scripts/weather.py` | Open-Meteo polling, automation rules engine, forecast publish |
| `pi/portal/portal.py` | Flask :80 — WiFi setup, `/pair`, `/api/history`, `/api/history/series` |
| `pi/tools/simulator.py` | Fake sensor data generator — use when no real edge nodes are attached |
| `pi/scripts/hivemq_bridge.py` | HiveMQ Cloud bridge (paho-mqtt) — replaces Mosquitto's native bridge, which never worked (see below) |
| `firmware/libraries/GreenhouseMesh/` | Shared mesh-relay library (`mesh_config.h` keys/trusted-nodes/tuning, `mesh_node.h` routing/relay logic) — 2026-07-09 session, see `docs/MESH_RELAY_EXPLAINED.md` |
| `deploy.ps1` | One command: scp + install + selftest on any Pi — **use this, not manual scp** |
| `app/lib/screens/history/history_screen.dart` | The chart screen (fl_chart) + custom date-range chip (2026-07-09) |
| `app/lib/utils/history_prediction.dart` | Trend-extrapolation + forecast-overlay prediction logic |
| `pi/shared/history_query.py` | Shared `query_points()` used by both `portal.py` (HTTP) and `recorder.py` (MQTT) — 2026-07-09, closes an old duplication gap |
| `pi/shared/push.py` | FCM push helper — `send_push()`, reads registered device tokens from a retained MQTT topic — 2026-07-10 |
| `app/lib/services/fcm_token_service.dart` | Registers/refreshes the device's FCM token over MQTT (retained) — 2026-07-10 |
| `app/lib/screens/weather/rule_form_dialog.dart` | The customizable rule builder dialog (any zone/metric/operator/threshold/duration/action/notify) — 2026-07-10 |
| `app/lib/models/weather_rule.dart` | Rule model — zone+metric split, optional action, optional duration, per-rule notify flag — rewritten 2026-07-10 |
| `docs/technical/00-INDEX.md` | Entry point to the 15-file OSI-level Greek technical deep-dive (protocol/hardware/security/db detail) — 2026-07-20 |
| `TODO.md` | Consolidated, code-verified list of designed-but-unbuilt and built-but-hardware-unvalidated work — 2026-07-20 |
| `IMPROVEMENTS.md` | Code-verified list of things that work but could be better (security/correctness/performance/process), each with `file:line` — 2026-07-20 |
| `.github/workflows/ci.yml` | pytest + flutter analyze/test on every PR — 2026-07-20, previously nothing ran automated |
| `docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md` + plan | Deep-sleep Phase 1 (leaf sleep) design + 6-task implementation plan (Tasks 1-5 done) — this session |
| `docs/superpowers/specs/2026-07-26-mesh-field-visualization-design.md` + plan | Mesh Map screen design + 6-task implementation plan (fully done) — this session |
| `firmware/libraries/GreenhouseMesh/mesh_node.h` | Wire format v2 (sleepy flag, battery/parent telemetry), RTC-persistent state helpers — this session |
| `app/lib/screens/devices/mesh_map_screen.dart` + `mesh_map/` | The live Mesh Map screen: layout engine, link painter, node card, pinned-position store — this session |
| `pi/tools/simulator.py` | Also now publishes `/mesh` topology JSON (fake shifting topology) — this session |
| `docs/superpowers/specs/2026-07-20-uart-bridge-design.md` + plan | UART-wired bridge design + 5-task plan, now approved+implemented (2026-07-27) |
| `firmware/bridge_esp32/bridge_esp32.ino` | Rewritten: UART (`Serial1`) instead of WiFi/MQTT/TLS, heartbeat liveness, battery/mesh telemetry over the wire — 2026-07-27 |
| `pi/scripts/serial_bridge.py` | New — reads the bridge's UART JSON stream, republishes to loopback Mosquitto — 2026-07-27 |
| `pi/systemd/greenhouse-serial-bridge.service` | New systemd unit for the above — 2026-07-27 |

---

## Scope status

The project was originally scoped as 6 slices (`docs/superpowers/specs/2026-06-25-greenhouse-app-connectivity-design.md` §2). Status against that scope, plus everything found since:

**Slice status:**
- 1 App + Connectivity — ✅ done
- 2 Field Firmware (ESP-NOW mesh, WROOM bridges) — firmware done, including 2026-07-09's dynamic multi-hop relay upgrade, 2026-07-26's Phase 1 deep-sleep + telemetry upgrade, and 2026-07-27's UART-wired bridge (full replacement — the old WiFi/MQTT/TLS bridge code no longer exists in the tree, only in git history); **still not field-validated on real sensor hardware** (simulator only — none of the relay/deep-sleep/UART-bridge code has ever been compiled/flashed, no toolchain in the dev sandbox); BLE pairing was planned but superseded by the working mDNS/QR discovery instead
- 3 Storage + History — ✅ done, reimplemented as a local SQLite recorder (not InfluxDB) + this session's chart feature
- 4 Automation + Alerts — ✅ done, and this session made it fully customizable: in-app rule builder (any zone/metric/operator/threshold/duration/action, Weather screen → Rules tab → "Add rule") instead of six hardcoded thresholds, plus a real fix for rule edits never having reached the Pi (see this session's TL;DR above)
- 5 Cloud Relay (multi-customer accounts, device registry, FCM push) — **partially done this session**: FCM push notifications now work (app closed/backgrounded still gets real alerts) via `pi/shared/push.py` + a retained-token registry topic; multi-customer accounts/device registry still **not started** — current remote access is still single-tenant HiveMQ Cloud
- 6 Field Hardening (solar/18650, IP65 enclosures, cellular fallback) — hardware mods **not started**, but the firmware duty-cycle side is now coded: 2026-07-26's deep-sleep Phase 1 (leaf sleep for battery nodes), see `docs/EDGE_NODE_POWER_OPTIMIZATION.md` and `docs/superpowers/plans/2026-07-26-mesh-deep-sleep.md`. Never compiled/flashed yet.

**Fixed this session (2026-07-08, later):** Mosquitto's native `connection` bridge to HiveMQ Cloud had never actually worked — 0 successful handshakes across 9 days of logs, a real Mosquitto bridge-code incompatibility with this HiveMQ cluster (not a quota/account issue). Any prior appearance of "remote access working" was the app displaying a stale retained value, not live data. Replaced with `greenhouse-hivemq-bridge.service` (small paho-mqtt forwarder script) — verified live two-way delivery, stable connection, automated round-trip check added to `selftest.sh` (now 26/26).

**Also fixed this session:** history charts now work remotely too. They previously called the Pi's HTTP `/api/history` directly, which only exists on the LAN (HiveMQ bridges MQTT, not HTTP) — so charts failed with "could not load" as soon as remote MQTT access started actually working and got tested. Added an MQTT request/response path (`greenhouse/history/request` → `greenhouse/history/response/<id>`, answered by `greenhouse-recorder`); the app now picks HTTP or MQTT based on whether it's connected local or remote. Verified end-to-end against the real HiveMQ cluster (409 real points returned).

**Detailed backlog: see `TODO.md` and `IMPROVEMENTS.md` instead of this
section.** This used to be a checklist duplicating both files and had
drifted out of sync with them (e.g. still claiming `/pair` was fully
unauthenticated after PIN-gated `/pair/confirm` had already shipped, and
still describing the ESP-NOW channel-follows-router-SSID dependency as an
open future risk after the UART-bridge work had already replaced it with a
fixed channel) — two backlogs disagreeing with each other is worse than
one. `TODO.md` covers designed-but-unbuilt and built-but-hardware-
unvalidated work; `IMPROVEMENTS.md` covers things that work but could be
better (security/correctness/performance), each with real `file:line`
references, kept current each session and cross-checked against the actual
tree rather than trusted checkbox state.

---

## Pi details (master unit)

| Item | Value |
|---|---|
| Hostname / mDNS | `greenhouse` / `greenhouse.local` |
| User / password | `pi` / per-unit random (`/boot/firmware/INITIAL_PASSWORD.txt`); use the SSH key |
| SSH key | `C:\Users\billy\.ssh\id_ed25519` (passwordless; baked into every image as admin key) |
| MQTT | 8883 TCP-TLS local (user `app`, per-unit password in `/etc/greenhouse/device.json`); HiveMQ Cloud bridge for remote |
| Pairing window | 600s after `greenhouse-portal` starts; restart the service to reopen (~20s to rebind port 80) |
| Recorder DB | `/var/lib/greenhouse/greenhouse.db` (SQLite, WAL mode) |
