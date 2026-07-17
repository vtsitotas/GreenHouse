# TODO — Unimplemented / Partially-Implemented Work

Consolidated from every design spec (`docs/superpowers/specs/`), implementation
plan (`docs/superpowers/plans/`), and backlog note (`HANDOFF.md`) in this repo,
cross-checked against the **actual current code** (not just what those docs
claim — several `HANDOFF.md` entries were stale against what's really in the
tree as of this pass). Superseded/obsolete plans are listed separately so they
don't get mistaken for live work.

---

## 1. Designed but zero code written

### Direct-to-Pi pairing + PIN authentication
**Spec:** `docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md`
**Status:** Proposed, approved in conversation, **no implementation plan or code yet**.

Lets a user pair the app directly against the Pi's setup hotspot without ever
configuring home WiFi (for sites with no ISP WiFi at all). Requires:
- `pi/portal/portal.py`: split `/pair` into `GET /pair` (existence check only)
  + new `POST /pair/confirm` (PIN-gated, returns credentials).
- `pi/scripts/first_boot.sh`: generate a per-unit 6-digit `pair_pin`.
- 5-attempt lockout + 1s throttle on `/pair/confirm`.
- `app/lib/screens/pairing/pairing_screen.dart`: new "connect directly"
  button + PIN entry step.
- `INSTRUCTIONS.md`: add PIN-label printing to the mass-production step.

This also closes a real, currently-live gap: **`/pair` today returns full
MQTT credentials over plaintext HTTP with no authentication check beyond a
600s boot-time window** (`pi/portal/portal.py:198-217`) — anyone who can
reach the LAN/hotspot within that window gets credentials, and mDNS/DNS-SD
discovery itself is spoofable (no identity guarantee). This was previously
explicitly deferred ("Out of scope... Leave `/pair` as-is" —
`docs/superpowers/plans/2026-06-26-security-hardening-and-captive-portal.md:21`)
but removing the AP-mode timer (this spec's Goal 4) makes it urgent to fix now.

---

## 2. Code exists, never validated on real hardware

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

---

## 3. HANDOFF.md backlog — verified against current code

### Security / access control
- [ ] `/pair` and `/api/history*` have no authentication beyond LAN/hotspot
      reachability + (for `/pair`) a 600s boot-time window — see §1 above,
      this is the fix in progress.
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

## 4. Superseded / obsolete plans — not actionable, kept for history only

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
