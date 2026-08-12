# TODO — Unimplemented / Partially-Implemented Work

Consolidated from every design spec (`docs/superpowers/specs/`), implementation
plan (`docs/superpowers/plans/`), and backlog note (`HANDOFF.md`) in this repo,
cross-checked against the **actual current code** (not just what those docs
claim — several `HANDOFF.md` entries were stale against what's really in the
tree as of this pass). Superseded/obsolete plans are listed separately so they
don't get mistaken for live work.

---

## 1. Designed but zero code written

### Direct-to-Pi pairing without home WiFi (AP-mode "connect directly")
**Spec:** `docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md`
**Status:** PIN authentication (Goal 5) is implemented — the rest (Goals 1-4)
is still open.

**Done:** the PIN-gated credential handoff, which closes a real, previously-live
gap — `/pair` used to return full MQTT credentials over plaintext HTTP with no
authentication beyond a 600s boot-time window, and mDNS/DNS-SD discovery
itself is spoofable (no identity guarantee):
- `pi/portal/portal.py`: `GET /pair` now returns only `{"found": true}`; new
  `POST /pair/confirm` (PIN-gated, returns the credentials `/pair` used to)
  with a 5-attempt lockout + 1s throttle.
- `pi/scripts/first_boot.sh`: generates a per-unit 6-digit `pair_pin` into
  `device.json`.
- `app/lib/screens/pairing/pairing_screen.dart`: discovery now prompts for
  the PIN before calling `/pair/confirm`.

**Still open** (Goals 1-4 — the actual "skip home WiFi entirely" feature):
- No AP-mode bypass of the 600s `/pair` window yet (`portal.py`'s `pair()`
  still applies the timer in both AP and STA mode) — needed for Goal 4
  (indefinitely reusable pairing without SSH).
- No new "Σύνδεση απευθείας" button / choice screen in the app — today a user
  can still reach `/pair` while connected to the Pi's hotspot via the
  existing "Find my greenhouse" button (nothing gates that on STA mode), but
  there's no dedicated UX for it and no first-time-flow screen offering
  "home WiFi" vs "direct" up front.
- `INSTRUCTIONS.md`: no PIN-label printing step added to the mass-production
  process yet.

---

## 2. Mesh protocol enhancements discussed but not yet written as specs

Smaller than the items above — captured here so the design thinking isn't
lost before someone formalizes it properly.

> **Scale limits (8-device cap, fleet-reflash to add a sensor) are analysed
> separately in [`docs/SCALING_AND_EXPANSION_IDEAS.md`](docs/SCALING_AND_EXPANSION_IDEAS.md)**
> — where they come from in the code, the end-to-end-encryption change that
> lifts both, and an explicit list of what would break. Design analysis only;
> nothing there is implemented, and the doc argues for *not* implementing it
> until an edge node reliably delivers a reading.

### Adaptive TTL (mesh routing) — ✅ DONE
**Discussed:** 2026-07-20, no spec file yet at the time. **Implemented:**
shipped in PR #12 (`527c71f`, "security/reliability" pass) — confirmed still
in the tree at `firmware/libraries/GreenhouseMesh/mesh_node.h`'s
`meshSendReading()` and `mesh_config.h`'s `MESH_TTL_MARGIN`/`MESH_MAX_TTL`.
This entry was stale (still describing it as a future proposal) until this
pass cross-checked it against the code. Left below for the historical
rationale, since it's still accurate — just already built, not proposed.

`MESH_MAX_TTL` used to be a fixed constant (4) in `mesh_config.h`, applied
the same to every packet regardless of how deep in the mesh it originated.
Real consequence: a packet from a node whose rank was ≥6 got silently
dropped one hop before reaching the bridge (`docs/technical/03-mesh-routing.md
§4` TTL walkthrough) — the network was structurally capped at ~5 hops deep
no matter the physical layout.

Fix: TTL is now **adaptive per-packet** instead of a global constant — set
from the origin node's own already-known rank plus a small margin, in
`meshSendReading()` (`mesh_node.h`):
```c
// before:
pkt.ttl = MESH_MAX_TTL;
// now:
pkt.ttl = (meshMyRank == MESH_RANK_UNROUTED) ? MESH_MAX_TTL
                                              : meshMyRank + MESH_TTL_MARGIN;  // MESH_TTL_MARGIN = 2
```
Zero wire-format change (the `ttl` field already existed at its current
size), zero new coordination needed (rank is already known locally). No
safety downside — the actual anti-loop protection is the strict-rank parent
rule, not TTL (`docs/technical/03-mesh-routing.md §4`), so a larger
effective TTL costs nothing. Removes the hard depth ceiling entirely; the
network can grow as deep as the physical mesh actually reaches.

### Clock synchronization for the deep-sleep shared wake window
**Discussed:** 2026-07-20, no spec file yet. Extends the still-unimplemented
deep-sleep plan in `docs/EDGE_NODE_POWER_OPTIMIZATION.md` and the
forward-compat `window_duration_ms` field already carried in every
`MeshBeacon` (`mesh_config.h` — carried today, unused).

**The problem:** once real deep sleep ships, nodes sleeping on independent
schedules would miss each other entirely unless they're reliably awake at
the same moments — but no node (except the always-on bridge) can keep an
accurate clock for the days/weeks between resyncs that battery deployment
implies.

**The design worked out in conversation:**
- Nodes don't need long-term clock accuracy — only need to resync **every
  wake cycle** against their parent's beacon, using the `beacon_interval_ms`
  field (already exists — "gap until sender's next beacon") to compute
  exactly how long to sleep until the next shared window. Drift never
  compounds across days, because every cycle re-anchors against a fresh
  reference point, cascading down from the bridge (always-on, stable clock)
  through each rank.
- Each wake cycle: brief **guard-listen window** (sized to cover one
  sleep-interval's worth of RTC drift, not cumulative drift) → hear parent's
  beacon → do own work (send reading / relay children's packets) → compute
  next sleep duration from the freshly-received `beacon_interval_ms` → deep
  sleep.
- Hardware suggestion: an external 32kHz watch crystal for RTC timing
  (instead of the ESP32's internal RC oscillator) shrinks worst-case
  per-cycle drift, letting the guard-listen window — and its battery cost —
  shrink too.
- Needs a new tuning constant (e.g. `MESH_WAKE_GUARD_MS`), sized
  empirically once real hardware drift is measured on actual boards.
- First-boot / no-known-parent case already has a conceptual answer in the
  existing mesh design (longer discovery listen before adopting any
  schedule, `docs/technical/03-mesh-routing.md`) — just needs to be wired
  into this scheme once deep sleep is actually built.

**Update 2026-07-26:** this design is now formally recorded as "Phase 2" in
`docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md` §Phase 2
(explicitly deferred — do not build until Phase 1's bench run produces real
per-board RTC-drift measurements; the Phase 1 wake logs collect exactly that
data). Phase 1 (leaf-only sleep, which sidesteps the clock-sync problem by
keeping every possible parent always-on) is implemented — see §4 Field
hardening below.

---

## 3. Code exists, never validated on real hardware

### UART-wired bridge (replace WiFi bridge uplink)
**Spec/plan:** `docs/superpowers/specs/2026-07-20-uart-bridge-design.md`,
`docs/superpowers/plans/2026-07-20-uart-bridge.md`
**Status:** Fully implemented 2026-07-27 (PR #15) — spec/plan Tasks 1-4
done. Task 5 (physical bench validation) is the only thing left, and it's
entirely the user's — no compiler or hardware access from this dev sandbox.

For deployments where the bridge and Pi sit physically close together,
replaces the bridge's WiFi+MQTT+TLS uplink with a direct 3-wire GPIO UART
connection (`firmware/bridge_esp32/bridge_esp32.ino`, `Serial1` on GPIO4
TX / GPIO5 RX, 115200 8N1 — not `Serial2`, corrected from the original
spec's placeholder since the C3 only has two hardware UARTs). Removes the
router dependency and the WiFi/MQTT credentials that were baked into the
bridge firmware. New `pi/scripts/serial_bridge.py` (18 tests, TDD) reads
the newline-delimited JSON stream and republishes to the existing loopback
Mosquitto with identical topics/payloads/retain to today — recorder/
weather/portal/app see no difference. Also carries the deep-sleep-era
battery/mesh-topology telemetry (added after the original spec was
written) and a heartbeat line that replaces the old MQTT Last-Will for
bridge-liveness detection, since the bridge is no longer an MQTT client
itself. The whole mesh fleet now locks to a fixed ESP-NOW channel instead
of scanning a router's SSID — edge nodes no longer need `secrets.h` at
all. New `greenhouse-serial-bridge.service` + `install.sh` wiring +
`INSTRUCTIONS.md` Part 6 (wiring diagram, one-time `raspi-config` step).

- [x] ~~Task 5 — bridge UART link~~ — **resolved 2026-08-10, was never actually
      broken.** The 2026-07-27 "zero bytes on `/dev/serial0`" was a
      diagnostic artifact: `head -c N /dev/serial0` buffers and gets killed
      by `timeout` before flushing, which reads as a dead link. An
      instrumented pyserial read shows the bridge heartbeating reliably (11
      lines in 20s at 115200). Wiring, power, and firmware were all correct
      the whole time. `firmware/bridge_esp32_wifi_fallback/` is no longer
      needed but kept as a fallback. See `docs/DEVICES.md`'s 2026-08-10 bench
      session and `HANDOFF.md`'s matching TL;DR for the full writeup.
      **Still genuinely open:** no edge node has been observed delivering an
      actual `reading` line over this now-confirmed-working link — see the
      real-hardware field test item above.

### Dynamic mesh relay (multi-hop ESP-NOW)
**Spec/plan:** `docs/superpowers/specs/2026-07-09-dynamic-mesh-relay-design.md`,
`docs/superpowers/plans/2026-07-09-dynamic-mesh-relay.md`
**Status:** Fully coded (`firmware/libraries/GreenhouseMesh/*.h`, both edge
sketches, bridge sketch) — **has never been compiled** (no `arduino-cli` in
the dev sandbox) or run on physical ESP32 hardware. See
`docs/MESH_RELAY_EXPLAINED.md` and the plan's Task 5 bench-test checklist.

### ESP32-CAM live view + motion alerts — ⏸️ PARKED (2026-08-02)
**Parked:** the camera never reached a useful state on real hardware, so the
whole vertical slice was moved to `parked/camera/` and unwired from the app,
`pi/install.sh` and CI. Nothing below is live work any more — it's kept as the
record of where bring-up stopped, in case the camera is ever revived. The
four camera entries in this file (this one, WebRTC Phase 2, `/stream` token
auth, and the streaming/motion-detection conflict further down) are all
parked together. Restore steps: `parked/camera/README.md`. Paths below are
pre-park; each file now sits under `parked/camera/` at the same relative path.

**Spec/plan:** `docs/superpowers/specs/2026-07-10-esp32-cam-integration-design.md`,
`docs/superpowers/plans/2026-07-11-esp32-cam-integration.md`
**Status at park time:** Code present and complete: `firmware/cam_esp32/cam_esp32.ino`,
`pi/scripts/cam_bridge.py`, `pi/shared/motion.py`, `pi/shared/cam_store.py`,
plus app-side `camera_screen.dart`/`camera_provider.dart`/`cam_status.dart`,
and tests (`pi/tests/test_cam_bridge.py`, `test_motion.py`, `test_cam_store.py`).
**Bench-testing started 2026-07-26, continued 2026-07-27** (first time on
real hardware — an AI-Thinker ESP32-CAM on an ESP32-CAM-MB downloader
base): the camera never connected at all during the 2026-07-26 session
(`greenhouse/cam/status` stuck at `online: false`) — root cause found
2026-07-27: `secrets.h` never existed for this sketch, so the firmware
couldn't even compile. Fixed: created it (gitignored, per-device), added
the missing Arduino-library junction (same pattern as `GreenhouseMesh`),
set a real `CAM_TOKEN` on the Pi (was still the install-time placeholder),
and fixed two real compile bugs found while actually trying to flash:
missing `#include <uri/UriBraces.h>`, and `PI_HOST` switched from
`greenhouse.local` to a direct IP (confirmed, not just suspected as of the
2026-07-26 entry: `HTTPClient` doesn't resolve mDNS reliably on this core —
same class of bug as the `mqtt_client` TLS-callback fix in the app). **It
compiles now; not yet flashed/bench-tested** — the earlier 2026-07-26
"flashing itself now succeeds" note describes a *prior* flash attempt of
firmware that, per the above, couldn't have actually been running
(compile-blocked by the missing `secrets.h`) — treat runtime behavior as
entirely unconfirmed until the current firmware is actually flashed and
observed. **Still open:** WiFi connect, the 3s snapshot-POST loop to the
Pi, and confirming the direct-IP fix actually resolves the snapshot path.
Also flagged but not yet hit in practice: `IMPROVEMENTS.md §B3` (LAN
live-view blocks motion detection) and the `CAM_TOKEN` file/firmware
hand-sync requirement (`/etc/greenhouse/cam_token.txt` must match the
flashed `secrets.h` exactly — now set to a real value, see above). Next
bench step: flash, then read the serial monitor
output at 115200 baud and continue from there.

### Phase 2 — WebRTC remote camera streaming — ⏸️ PARKED
Documented in the ESP32-CAM design spec's Phase 2 section, **deliberately
not planned or started**. Would need `aiortc` on the Pi + a new public TURN
relay server; flagged risk: Pi Zero W may lack CPU headroom to encode a live
WebRTC track (no hardware video encoder) — needs a bench test before
committing to this track.

### ESP32-CAM `/stream` token auth (app-side half of IMPROVEMENTS.md A5) — ✅ BUILT 2026-07-28, now ⏸️ PARKED with the camera
The work was finished before the camera was parked: every endpoint on the
camera is `CAM_TOKEN`-gated, `/stream` included. The cross-stack chain got
built end to end — `portal.py`'s `_pairing_payload()` returns `cam_token` →
`ConnectionConfig.camToken` → `camera_screen.dart`'s `streamUrl()` appends
`?token=` → `cam_esp32.ino`'s `handleStream()` calls `checkCamToken()`. The
token rides the already-PIN-gated `/pair/confirm` response, so no new
user-facing step; the manual-entry path takes it from the pairing screen's
Advanced section.

As of 2026-08-02 the camera halves of that chain live under
`parked/camera/` (`app/lib/screens/camera/camera_screen.dart`,
`firmware/cam_esp32/cam_esp32.ino`) and are not built or flashed. The Pi
and app plumbing that survived the parking — `_pairing_payload()`'s
`cam_token` field and `ConnectionConfig.camToken` — is inert but harmless,
and means a restore per `parked/camera/README.md` gets an already-gated
`/stream` rather than the open one this entry originally tracked. Nothing
here was ever bench-tested on real camera hardware.

**Bench-test note:** the Pi and app halves are covered by tests, but the
firmware half (`handleStream()`'s new gate) shares the general caveat that
nothing in `firmware/` has been compiled or flashed from this sandbox —
confirm the live view still loads once the camera is actually flashed.

### Adaptive ESP-NOW channel discovery for edge nodes (IMPROVEMENTS.md B5) — ✅ RESOLVED (differently than proposed)
Edge nodes used to find their ESP-NOW channel by scanning for the hardcoded
home-router `WIFI_SSID` (`edge_node_esp32.ino`, `edge_node_esp32_c3.ino`)
even though they never actually joined WiFi — renaming the router forced a
reflash of every node. The proposed fix below (scan for the bridge's own
beacon instead of the router's SSID) was never built — instead, the
2026-07-27 UART-wired-bridge work (`docs/superpowers/specs/2026-07-20-uart-bridge-design.md`)
made the whole problem moot: every node (bridge + both edge variants) now
locks to a fixed constant, `MESH_FIXED_CHANNEL` in `mesh_config.h`, with no
scanning at all — confirmed via `grep` across both edge sketches, no
`WIFI_SSID` reference remains in either. This was a side effect of removing
the router dependency for the UART deployment mode, not a deliberate fix
of this specific finding, but it closes it: renaming the router (or having
none at all) no longer requires reflashing any node. Kept below for
historical context (the originally-proposed alternative was never built).

Original proposal (not implemented, superseded): scan all 13 channels
listening for the bridge's own beacon (`MESH_MAGIC`, rank 0) instead of the
router's SSID — decouples the mesh from router config entirely.

### LAN camera streaming blocks motion detection (IMPROVEMENTS.md B3) — ⏸️ PARKED
`cam_esp32.ino`'s `WebServer` is single-threaded; `handleStream()`'s
`while (client.connected())` loop means `loop()` (and therefore
`sendSnapshotToPi()`) never runs while someone is watching the live MJPEG
view — no motion detection and no heartbeat for the whole viewing session
(the Pi will even mark the camera "offline" after ~9s of streaming). Fix
needs either a switch to `ESPAsyncWebServer` or a periodic yield inside the
stream loop that sneaks in a snapshot POST — both are real behavioral
changes to the streaming path that need a physical camera to validate
(motion detection continuing to work *during* a live view, not just after
the client disconnects). Not attempted in this pass for the same
untestable-without-hardware reason as the channel discovery item above.

---

## 4. HANDOFF.md backlog — verified against current code

### Security / access control
- [x] `/pair` had no authentication beyond LAN/hotspot reachability + a 600s
      boot-time window — fixed: `/pair` now only confirms existence, real
      credentials require the PIN via `POST /pair/confirm` (see §1 above).
- [x] `/api/history*` had no authentication beyond LAN/hotspot reachability —
      fixed 2026-07-28: per-unit `api_token` (generated in `first_boot.sh`,
      backfilled onto existing units by `install.sh`), required as
      `Authorization: Bearer`, constant-time compared, fail-closed. Delivered
      to the app inside the already-PIN-gated `/pair/confirm` response.
- [x] **`POST /cam/frame` was completely unauthenticated** — found and fixed
      2026-07-28; the most serious hole in the system. One POST from any LAN
      host hijacked the Pi's notion of the camera's IP, which then leaked
      `CAM_TOKEN` (the token authorizing `DELETE /event/<id>`) to the
      attacker on the next fetch, relayed attacker images to the app, and
      allowed unbounded motion-alert/push spam. See `IMPROVEMENTS.md §Α7`.
- [ ] No per-customer/multi-tenant device registry — confirmed: one shared
      HiveMQ Cloud account hardcoded for the entire fleet
      (`pi/install.sh:105-112`). Current model is single-tenant.
- [ ] ESP-NOW mesh uses one network-wide PMK/LMK key pair, not per-node keys
      (`firmware/libraries/GreenhouseMesh/mesh_config.h:39-50`) — defends
      against a nearby stranger device, not against a physically-captured
      node's key being extracted. Documented, accepted limitation for thesis
      scope (see `docs/technical/10-security.md §8`).

### Firmware / field hardware
- [ ] **No actuator controller firmware exists.** `greenhouse/actuators/<id>/set`
      is published correctly by both the app and `weather.py`'s rule engine,
      but nothing in this repo subscribes to it and drives a real relay/pump/
      fan — only `pi/tools/simulator.py` fakes actuator state today. Confirmed
      via full repo search (`docs/technical/05-mqtt-broker.md §5`). This is a
      bigger gap than `HANDOFF.md` implies — it's not listed there explicitly.
- [~] Real-hardware test of the full ESP-NOW → bridge → MQTT path — **partly
      done on the bench 2026-08-10.** Proven with instrumented reads:
      bridge ESP32 (`206EF16CBE80`) → UART → `serial_bridge.py` → Mosquitto →
      `recorder.py` → SQLite all work, and the bridge heartbeats reliably
      (11 lines in 20 s at 115200 on `/dev/serial0` → `ttyS0`).
      **Not yet proven: any edge node actually delivering a reading.** A 20 s
      capture showed `heartbeat` lines only and zero `reading` lines, and no
      mesh rows landed in the recorder over a 40 s window. The `zone1`/`zone2`
      values visible in MQTT are *retained* messages from an earlier session,
      not live data — retained topics read back instantly on subscribe and are
      easy to mistake for a working feed. Close this out by powering an edge
      node (post GPIO1 rewire + reflash) and confirming `reading` lines appear
      on the UART and new rows appear in `readings`.
- [ ] Bench-test an edge node running the **real** `edge_node_esp32_c3.ino`
      end to end. The soil pin moved GPIO2 → GPIO1 on 2026-08-10 (GPIO2 is a
      C3 strapping pin whose board pull-up pins the ADC at 4095), so every
      already-wired C3 node needs the AOUT wire moved **and** a reflash before
      its soil readings mean anything. DHT22 + soil both verified good on the
      bench via `firmware/sensor_pin_test/`.
- [x] ~~Bench Pi on stale (2026-07-27) code, 7 selftest failures~~ — **fixed
      2026-08-10.** Full `deploy.ps1` run against the live unit surfaced one
      more real bug in the process: `gen_certs.sh`'s mosquitto-cert guard
      (`[ -f server.crt ] && exit 0`) sat *before* the portal HTTPS keypair
      copy, so any unit whose mosquitto cert predated that copy step — this
      one — could never get a portal keypair from any later `install.sh` run.
      Split into two independent checks; verified the mosquitto cert stays
      byte-identical (sha256) while the portal copy self-heals. `selftest.sh`:
      **45 passed, 0 failed.**
      Still true and *not* touched by this deploy (it only backfills missing
      `device.json` fields, it doesn't rotate existing ones): the `app` MQTT
      password on this unit is still `123`. Run `rotate_secrets.sh` if/when
      that matters — deferred since it would invalidate any already-paired
      app session.
- [ ] Field hardening: solar/18650 battery power, IP65 enclosures, cellular
      fallback — hardware side **not started**, but the firmware side now
      exists: deep-sleep Phase 1 (leaf sleep) is fully coded per
      `docs/superpowers/plans/2026-07-26-mesh-deep-sleep.md` Tasks 1-5
      (sleepy wake cycle in both edge sketches, RTC-persistent mesh state,
      battery ADC, bridge telemetry/per-role offline windows). **Never
      compiled or bench-tested on real hardware** — that's the plan's Task 6
      checklist, plus the physical mods (LED desolder, divider soldering)
      from `docs/EDGE_NODE_POWER_OPTIMIZATION.md §1`.
- [ ] Golden SD image + clone path unproven on a second physical unit.

### ML / analytics
- [ ] "ML watering prediction" — never scoped beyond the original wishlist
      mention, no code anywhere.
- [ ] Nightly export of recorder data to an external store (Postgres/Supabase)
      for forecast-accuracy comparison and monthly stats — deliberately
      deferred in the sensor-database spec (push-based design, not
      dependent on external uptime). Not started.

### App feature gaps
- [ ] Screen-by-screen UX enhancement pass for dashboard, control, devices,
      pairing, settings — only the history screen (2026-07-08/09 sessions),
      the weather/rules screen (2026-07-10 rule builder), and now the
      devices screen (2026-07-26 Mesh Map entry point + screen, see
      `docs/technical/13-mobile-app.md §11`) have had this treatment so far.
- [x] Devices screen gained a live Mesh Map (hub icon in the `AppBar` →
      `MeshMapScreen`): topology, RSSI-colored links, drag-to-pin,
      battery/sleepy badges — app side is fully implemented and tested
      against `pi/tools/simulator.py`'s `/mesh` topic.
- [x] Firmware-side `/mesh` and `/battery` publishing — implemented
      (bridge publishes both, plus its own root `/mesh` record and an MQTT
      LWT; wire-format v2 carries battery mV / parent MAC / parent RSSI).
      Caveat: like all firmware in this repo, never compiled or flashed on
      real hardware yet — until the fleet is reflashed (deep-sleep plan
      Task 6), the Mesh Map's degraded-data banner still shows on the real
      fleet.
- [ ] CSV export of history data — not started.
- [ ] Smartwatch/widget glance view — not started.
- [ ] iOS — completely untested (Android-only device used throughout
      development).
- [ ] No direct test exercises the forecast-timeout/failure fallback path in
      `historyWithPredictionProvider` (reviewed as correct by code review,
      not test-proven).

### Housekeeping
- [ ] `debugPrint()` calls still present in `mqtt_connection.dart` (×3),
      `connection_provider.dart` (×3), `notification_service.dart` (×1) —
      confirmed still in the tree, should be removed before any demo/release
      build.
- [ ] `WEATHER_INTERVAL=30` (30 seconds) is still set on
      `greenhouse-weather.service` — confirmed still the debug value, not a
      production-appropriate interval (the default in code is 1800s).

---

## 5. Superseded / obsolete plans — not actionable, kept for history only

These plans describe approaches the project **explicitly abandoned** in favor
of what's actually built today. Do not implement anything from them without
first checking whether it still applies.

- **`docs/superpowers/plans/2026-06-26-ux-fixes-tailscale.md`** — proposes
  Tailscale for remote access. Superseded entirely by the HiveMQ Cloud bridge
  (`docs/technical/08-cloud-bridge.md`); `docs/ARCHITECTURE.md` explicitly
  notes "όχι Tailscale". Also references a port-8080 portal, which no longer
  exists (portal binds directly to port 80 today).
- **`docs/superpowers/plans/2026-06-26-zero-touch-setup.md`** — proposes
  hostapd/dnsmasq for the AP and a port-8080 portal. Superseded by
  `2026-06-26-security-hardening-and-captive-portal.md`, which explicitly
  deletes `ap_mode.sh` and `provision.sh` from this earlier plan and replaces
  them with the NetworkManager-based `ap_up.sh` + `install.sh` that ship
  today.

---

## Verification notes

Everything else tracked in `docs/superpowers/specs/` /
`docs/superpowers/plans/` (app connectivity, sensor database, history chart
+ custom date range, customizable alert rules, FCM push notifications,
captive-portal security hardening) was cross-checked against real files in
`pi/`, `app/lib/`, and `firmware/` during this pass and confirmed **done**
— not repeated here. Checkbox state inside the plan `.md` files themselves is
**not** a reliable signal in this repo (every plan file has 0 boxes checked
regardless of actual completion) — this list was built by reading the real
source tree, not by trusting `- [ ]` markers.
