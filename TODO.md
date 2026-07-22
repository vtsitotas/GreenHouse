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

### UART-wired bridge (replace WiFi bridge uplink)
**Spec/plan:** `docs/superpowers/specs/2026-07-20-uart-bridge-design.md`,
`docs/superpowers/plans/2026-07-20-uart-bridge.md`
**Status:** Proposed, spec + 5-task implementation plan written, **no code
yet**.

For deployments where the bridge and Pi will always sit physically close
together, replaces the bridge's WiFi+MQTT+TLS uplink with a direct 3-wire
GPIO UART connection (both ESP32 and Pi GPIO run 3.3V logic — no level
shifter needed). Removes the router dependency and the WiFi credentials
currently baked into `bridge_esp32.ino` (the same committed-secret problem
flagged in `IMPROVEMENTS.md §Α1`). Newline-delimited JSON over the wire; a
new `pi/scripts/serial_bridge.py` republishes to the existing loopback
Mosquitto exactly as the current bridge does today, so nothing downstream
(recorder/weather/portal/app) needs to change. Also switches the ESP-NOW
mesh to a fixed radio channel instead of scanning a router's SSID, since no
router is assumed present in this deployment mode. Explicitly scoped to the
bridge↔Pi hop only — the Pi's own upstream/HiveMQ connectivity is untouched.

---

## 2. Mesh protocol enhancements discussed but not yet written as specs

Smaller than the items above — captured here so the design thinking isn't
lost before someone formalizes it properly.

### Adaptive TTL (mesh routing)
**Discussed:** 2026-07-20, no spec file yet.

Today `MESH_MAX_TTL` is a fixed constant (4) in `mesh_config.h`, applied the
same to every packet regardless of how deep in the mesh it originated. Real
consequence: a packet from a node whose rank is ≥6 gets silently dropped one
hop before reaching the bridge (`docs/technical/03-mesh-routing.md §4`
TTL walkthrough) — the network is structurally capped at ~5 hops deep no
matter the physical layout.

Proposed fix: make TTL **adaptive per-packet** instead of a global constant
— set it from the origin node's own already-known rank plus a small margin,
in `meshSendReading()` (`mesh_node.h`):
```c
// today:
pkt.ttl = MESH_MAX_TTL;
// proposed:
pkt.ttl = meshMyRank + MESH_TTL_MARGIN;   // e.g. MESH_TTL_MARGIN = 2
```
Zero wire-format change (the `ttl` field already exists at its current
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

Not yet written into `EDGE_NODE_POWER_OPTIMIZATION.md` itself — this TODO
entry is the only record of the design until someone formalizes it there or
into a dedicated spec.

---

## 3. Code exists, never validated on real hardware

### Dynamic mesh relay (multi-hop ESP-NOW)
**Spec/plan:** `docs/superpowers/specs/2026-07-09-dynamic-mesh-relay-design.md`,
`docs/superpowers/plans/2026-07-09-dynamic-mesh-relay.md`
**Status:** Fully coded (`firmware/libraries/GreenhouseMesh/*.h`, both edge
sketches, bridge sketch) — **has never been compiled** (no `arduino-cli` in
the dev sandbox) or run on physical ESP32 hardware. See
`docs/MESH_RELAY_EXPLAINED.md` and the plan's Task 5 bench-test checklist.

### ESP32-CAM live view + motion alerts
**Spec/plan:** `docs/superpowers/specs/2026-07-10-esp32-cam-integration-design.md`,
`docs/superpowers/plans/2026-07-11-esp32-cam-integration.md`
**Status:** `HANDOFF.md` (last updated 2026-07-11) says this is only
*designed*, not implemented — **that entry is stale.** The code is actually
present and appears complete: `firmware/cam_esp32/cam_esp32.ino`,
`pi/scripts/cam_bridge.py`, `pi/shared/motion.py`, `pi/shared/cam_store.py`,
plus app-side `camera_screen.dart`/`camera_provider.dart`/`cam_status.dart`,
and tests (`pi/tests/test_cam_bridge.py`, `test_motion.py`, `test_cam_store.py`).
**Still open:** firmware (Task 8 of the plan) has never been flashed to a
physical ESP32-CAM or bench-tested — same caveat as the mesh relay above.
Update `HANDOFF.md` to reflect this once confirmed.

### Phase 2 — WebRTC remote camera streaming
Documented in the ESP32-CAM design spec's Phase 2 section, **deliberately
not planned or started**. Would need `aiortc` on the Pi + a new public TURN
relay server; flagged risk: Pi Zero W may lack CPU headroom to encode a live
WebRTC track (no hardware video encoder) — needs a bench test before
committing to this track.

### ESP32-CAM `/stream` token auth (app-side half of IMPROVEMENTS.md A5)
`/capture` and `GET|DELETE /event/<id>` are now `CAM_TOKEN`-gated (see A5) —
the Pi is the only caller for those three, so the fix was self-contained.
`/stream` is the one endpoint the **app** calls directly
(`camera_screen.dart:113`, LAN direct view) and it's still unprotected: the
app has no way to learn `CAM_TOKEN` today. Full fix needs: `cam_token` added
to `/pair`'s response schema (`portal.py`'s `_pairing_payload()`, reading
from a new field in `device.json` or the existing `cam_token.txt`),
`ConnectionConfig.camToken`, `pairing_screen.dart`'s manual/QR entry, and
`camera_screen.dart` appending `?token=` to the stream URL. Left out of the
A5 pass because it's cross-stack (firmware + Pi + app) and untestable here
without real camera hardware — bench-test each hop before shipping this.

### Adaptive ESP-NOW channel discovery for edge nodes (IMPROVEMENTS.md B5)
Edge nodes currently find their ESP-NOW channel by scanning for the
hardcoded home-router `WIFI_SSID` (`edge_node_esp32.ino`,
`edge_node_esp32_c3.ino`) even though they never actually join WiFi —
renaming the router forces a reflash of every node. Proposed fix: scan all
13 channels listening for the bridge's own beacon (`MESH_MAGIC`, rank 0)
instead of the router's SSID — decouples the mesh from router config
entirely. Not attempted in this pass: changes the edge nodes' boot-time
channel-acquisition logic, which is exactly the kind of change that's
risky to get subtly wrong without a physical bench test (nodes that can't
find their channel don't join the mesh at all — silent failure, hard to
diagnose remotely).

### LAN camera streaming blocks motion detection (IMPROVEMENTS.md B3)
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
- [ ] `/api/history*` still has no authentication beyond LAN/hotspot
      reachability — not covered by the PIN fix (read-only history data, a
      smaller exposure than credential handoff was).
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
- [ ] Real-hardware field test of the full ESP-NOW → bridge → MQTT path —
      everything validated so far is against `tools/simulator.py`, not real
      sensor nodes in a real greenhouse.
- [ ] Field hardening: solar/18650 battery power, IP65 enclosures, cellular
      fallback — **not started**. `docs/EDGE_NODE_POWER_OPTIMIZATION.md` is a
      plan document only; zero deep-sleep code exists in any `.ino` file yet
      (confirmed — edge nodes still loop with `delay()`, radio always on).
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
      pairing, settings — only the history screen (2026-07-08/09 sessions)
      and the weather/rules screen (2026-07-10 rule builder) have had this
      treatment so far.
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
