# Greenhouse IoT — Session Handoff Archive

Older session TL;DRs, moved out of `HANDOFF.md` on 2026-08-07 to keep that
file focused on current state. These are historical session logs, not live
status — for what's actually true today, see `HANDOFF.md`, `TODO.md`, and
`IMPROVEMENTS.md`, all of which were built by reading the real tree rather
than trusting old notes (several entries below were themselves already
corrected against stale claims in earlier versions of this file — see the
2026-07-20 entry).

Newest to oldest.

---

## TL;DR of this session (2026-07-27, UART-wired bridge)

Implemented the already-approved `docs/superpowers/specs/2026-07-20-uart-bridge-design.md`
+ `docs/superpowers/plans/2026-07-20-uart-bridge.md` end to end (Tasks 1-4;
Task 5 is the user's physical bench pass, not run), merged as **PR #15**
(`3fb6112`). Triggered by the user asking how to wire the bridge
(ESP32-C3 SuperMini) directly to the Pi Zero W's GPIO header instead of
over WiFi — the answer led straight to this pre-existing, unimplemented
spec/plan, and the user asked to build it.

Replaces the bridge's WiFi+MQTT+TLS uplink with a direct 3-wire UART
connection to the Pi's GPIO header — no router, no WiFi/MQTT credentials
on the bridge firmware at all anymore.

- **Firmware** (`firmware/bridge_esp32/bridge_esp32.ino`, rewritten):
  drops `WiFiClientSecure`/`PubSubClient` entirely; prints newline-
  delimited JSON over `Serial1` (GPIO4 TX / GPIO5 RX, 115200 8N1). Two
  corrections made during implementation, since the 2026-07-20 spec
  predates work merged since: (1) `Serial2` doesn't exist on the ESP32-C3
  (only two hardware UARTs) — the spec's placeholder was wrong for this
  specific chip, corrected to `Serial1` with concrete pins; (2) the JSON
  protocol also carries `battery`/`mesh`/`heartbeat` message types beyond
  the spec's original `reading`/`status` pair, because the mesh deep-sleep
  telemetry work (2026-07-26, PR #13) added bridge-side battery/topology
  publishing and an MQTT Last-Will that this rewrite would otherwise have
  silently regressed. The heartbeat line (piggybacked on the existing
  rank-0 beacon cadence) is the UART-link replacement for that Last-Will,
  since the bridge is no longer an MQTT client itself and a wired serial
  link has no "connection" state of its own to hang liveness off of.
- Every mesh node (bridge + both edge variants) now locks to a fixed
  ESP-NOW channel (`MESH_FIXED_CHANNEL`, `mesh_config.h`) instead of
  scanning for a router's SSID — there's no router in this deployment
  mode. Edge nodes no longer need `secrets.h` at all.
- **`pi/scripts/serial_bridge.py`** (new): reads the UART JSON stream,
  republishes to the existing loopback Mosquitto with identical topics/
  payloads/retain to the old WiFi bridge — recorder/weather/portal/app see
  no difference. 18 new tests (mocked serial port), TDD. Plus
  `greenhouse-serial-bridge.service` (sandboxed like its siblings) and
  `install.sh` wiring (prints, doesn't automate, the one-time
  `raspi-config` step needed to free `/dev/serial0` from the login
  console — editing boot config unattended risks locking a unit out of
  serial-console access).
- Docs: `INSTRUCTIONS.md` gained a wiring-diagram section (Part 6);
  `docs/technical/04-bridge-gateway.md` fully rewritten (was already
  stale even before this change — missing the deep-sleep telemetry
  additions); `ARCHITECTURE.md`, `01-sensor-node-hardware.md`,
  `14-network-reference.md` synced; the design spec marked approved +
  implemented with both corrections noted inline.

**Process note:** the safety-critical firmware pieces (wire protocol, the
fixed-channel change across the whole fleet, the heartbeat-liveness
redesign) were written directly rather than delegated — same reasoning as
the 2026-07-26 deep-sleep session: a subtle firmware bug here only
surfaces on a real bench day. `serial_bridge.py` and the systemd/
install.sh wiring were sub-agent-implemented in parallel, then fully
reviewed before push; the systemd-wiring agent caught and fixed a stale
`/dev/ttyACM0` reference in `INSTRUCTIONS.md` on its own initiative. One
CI failure occurred and was fixed directly: `pyserial` (needed by the new
`serial_bridge.py`/its tests) was installed ad-hoc in the sub-agent's
sandbox but never added to `.github/workflows/ci.yml`'s hardcoded pip
list, so `test_serial_bridge.py` failed to import in CI — added.

**Not done in this session:** any deployment to real hardware. This dev
sandbox cannot reach `greenhouse.local` (confirmed — it's mDNS on the
user's home LAN, unreachable from this cloud container), so the Pi/phone
update is entirely the user's next step (`deploy.ps1` for the Pi,
`flutter build apk --release` + `flutter install` for the phone), same as
every prior session's deployment step.

---

## TL;DR of this session (2026-07-26, mesh deep sleep + field visualization + cam bench start)

Two features, both taken all the way from brainstorm → design spec →
implementation plan → full implementation (via background Sonnet
sub-agents, each reviewed before push), merged as **PR #13** (`a79d9ce`).
Then started real-hardware bench debugging of the ESP32-CAM (see below).

### 1. Mesh deep sleep — Phase 1 (leaf sleep), for real battery-life energy optimization
Spec: `docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md`. Plan:
`docs/superpowers/plans/2026-07-26-mesh-deep-sleep.md`.

Extends the 2026-07-09 dynamic mesh relay so battery-powered ("sleepy")
edge nodes deep-sleep 15 minutes between readings instead of looping with
the radio always on (`docs/EDGE_NODE_POWER_OPTIMIZATION.md`'s ~290µA
average-draw target, 200+ days on an 18650). Key design constraint: a
sleepy node must **never** be relied on as a parent (its radio is about to
go dark), so the fix is one new routing rule — a beacon's new `flags` bit
marks the sender sleepy, and sleepy-flagged beacons are recorded for
neighbor liveness but never adopted as a parent. Everything else in the
existing mesh (strict-rank loop safety, trickle beaconing, TTL/dedup) is
untouched.

Implemented (Tasks 1-5 of 6; Task 6 is the physical bench checklist, not
run yet — no compiler/hardware in the dev sandbox, same caveat as every
other firmware slice in this project):
- **Wire format v2:** `MeshBeacon` 18→19 bytes (sleepy flag),
  `MeshDataPacket` 23→33 bytes (battery millivolts, origin's parent MAC,
  parent RSSI, flags — all origin-owned, relays never rewrite them). Not
  backward compatible; the whole fleet reflashes together, same as the
  original mesh rollout.
- **RTC-persistent state** across deep sleep: seq counters (protects the
  bridge's de-dup cache from resetting to 0 every wake), a parent hint
  (revalidated, not trusted — its timeout is pinned to the trickle ceiling
  so it can't fire spuriously inside a ≤10s wake), the WiFi channel
  (skips a ~2s scan most wakes), and the 10-reading buffer.
- **The sleepy wake cycle** (both edge sketches — role picked by the
  node's own MAC in `TRUSTED_NODES[]`, so one firmware image serves both
  roles): sensor warm-up runs concurrently with radio bring-up; one
  sleepy-flagged wake beacon trickle-resets nearby awake neighbors so the
  parent (or a new candidate) is heard inside the warm-up window; a
  TX-confirm check with a bounded orphan-rediscovery fallback; a hard 10s
  max-awake backstop where every exit path (including `esp_now_init()`
  failure — sleeps, never restart-loops) persists state and sleeps.
  Two guarantees added beyond the plan after tracing failure cases: an
  unconfirmed reading is re-queued with the **same** sequence number (the
  bridge's existing de-dup drops it if the "failure" was only a lost ack,
  otherwise the resend delivers it), and a **stale-channel self-heal** — 2
  consecutive silent wakes force a full SSID rescan, so a router channel
  change while a node sleeps can't strand it permanently.
- **Bridge:** per-role offline windows (45 min for sleepy nodes, 15s for
  always-on, same `MESH_OFFLINE_AFTER` multiplier applied to a per-role
  expected interval), a LiFePO4 millivolt→percent curve, retained
  `greenhouse/nodes/<MAC>/battery` + `/mesh` topology JSON publishing, the
  bridge's own root `/mesh` record, and an MQTT Last-Will on its own
  `/status` topic (nothing reported bridge liveness at all before this).
- **Battery sensing:** a 2×220kΩ resistor divider on a spare ADC pin,
  8-sample `analogReadMilliVolts()` average; an unfitted (mains) node reads
  under 2000mV and is reported as "not measured", not zero.

### 2. Live mesh field visualization — Mesh Map screen
Spec: `docs/superpowers/specs/2026-07-26-mesh-field-visualization-design.md`.
Plan: `docs/superpowers/plans/2026-07-26-mesh-field-visualization.md`.

A new **Mesh Map** screen in the Flutter app (Devices screen → hub icon):
every node (bridge + sensors) as an icon card on a pannable/zoomable
canvas, connected to its parent by an animated RSSI-colored link, showing
battery %, zone, online/offline, and a sleepy-node moon badge —
auto-laid-out by mesh rank with drag-to-pin (persisted in
`SharedPreferences`). Fully implemented, all 6 plan tasks:
- `pi/tools/simulator.py` publishes the same `/mesh` MQTT contract the
  real firmware now targets, with a small shifting fake topology (one node
  periodically re-parents) — so the whole screen was buildable and
  demoable **before** any real firmware existed, and still works without
  hardware today.
- `NodeStatus` (`app/lib/models/node_status.dart`) gained mesh fields +
  `fromMqttMesh`; `MqttConnection` routes the new `/mesh` topic.
- `GreenhouseRepository`'s merge became source-aware (`NodeStatusSource`
  enum): `/status` exclusively owns online/offline, `/battery` and `/mesh`
  events fold their own facets into the same node entry without ever
  flipping liveness.
- New `app/lib/screens/devices/mesh_map/`: a pure rank-row layout engine
  (with pinned-position override), an RSSI→color/dashed link-quality
  mapping + animated `CustomPainter`, the node card widget, and a
  `SharedPreferences`-backed pinned-position store — then the screen
  itself (pan/zoom, drag-to-pin, tap-for-detail-sheet, a degraded-data
  banner for when no `/mesh` data is being published yet).
- 30+ new Flutter tests + 12 new Python tests, all green in CI.

**Process note on this session's sub-agent use:** implemented via ~9
background Sonnet sub-agents dispatched in dependency-respecting waves
(simulator/model/routing in parallel, then repository-merge + layout/
painter/card in parallel, then the screen, then the entry point), each
reviewed against the spec before its commit was pushed. Two CI failures
occurred and were fixed directly (both `unnecessary_import` lints in new
test files — `dart:async`/`dart:ui` already re-exported by
`flutter_test.dart`) and one widget-test overflow (the detail bottom sheet
needed to be scrollable). The deep-sleep firmware's most safety-critical
pieces (wire format, the sleepy routing rule, RTC persistence) were written
directly rather than delegated, given the cost of a subtle firmware bug
only surfacing on a real bench day; the two edge sketches and the bridge
changes were sub-agent-implemented then reviewed, and one real gap a
sub-agent's own failure-case trace surfaced (no recovery from a router
channel change during sleep) was fixed immediately after review, before
push.

### 3. ESP32-CAM bench debugging — started, not concluded
The camera feature (`firmware/cam_esp32/cam_esp32.ino` +
`pi/scripts/cam_bridge.py` + app-side camera screen) had **never once been
on physical hardware** before this session (see `TODO.md §3`, previously
accurate). User began real bench-testing on an AI-Thinker ESP32-CAM +
ESP32-CAM-MB downloader base:
- Code-review pass (no hardware access from this environment) surfaced
  several real risks worth having in mind at the bench: `HTTPClient`
  POSTing to `greenhouse.local` (mDNS-name resolution from the ESP32 side
  isn't guaranteed — the leading suspect if snapshot POSTs fail with
  `code=-1`); the already-documented `IMPROVEMENTS.md §B3` (LAN live
  view's blocking `handleStream()` loop starves motion detection for the
  whole viewing session); the `CAM_TOKEN` hand-sync requirement between
  the flashed `secrets.h` and `/etc/greenhouse/cam_token.txt`; and no WiFi
  reconnect watchdog on the cam sketch (the bridge got one in an earlier
  session for the same class of failure, `IMPROVEMENTS.md §B4`).
- First flash attempt failed with a memory-related error at upload time
  (exact text not captured); on a later attempt it flashed successfully
  without a clearly isolated fix — not investigated further since it
  stopped recurring. **This is the current state: it flashes, runtime
  behavior (WiFi connect / snapshot loop / MQTT online status) not yet
  observed** — next step is reading the serial monitor at 115200 baud.
  **Do not treat the camera feature as validated on real hardware yet** —
  update this entry and `TODO.md §3` again once the serial output is in.

*(Note, 2026-08-02: the camera was later parked — see `HANDOFF.md`. This
bench-debugging session never concluded before that decision.)*

---

## TL;DR of this session (2026-07-20, technical docs + design specs + CI)

Long documentation/design session, triggered by the user wanting deep
OSI-level technical references for the thesis writeup. No feature code
touched — everything below is docs, specs, plans, or small config/security
cleanups. 8 PRs, all merged.

**`docs/technical/` — new 15-file Greek technical deep-dive** (`00-INDEX.md`
through `14-network-reference.md`): sensor hardware, ESP-NOW protocol at
OSI-layer detail, the mesh routing algorithm (rank/beacon/trickle), the
bridge gateway, the MQTT broker (full topic tree + QoS/retain policy), why
SQLite over MariaDB/InfluxDB, the recorder's buffering/rollup, why a custom
paho-mqtt HiveMQ bridge replaced Mosquitto's native (broken) one, the setup
portal + full mDNS/DNS-SD explanation, an end-to-end security/TLS map, the
weather automation engine, the camera/motion pipeline, the Flutter app
architecture, and a consolidated port/protocol/OSI reference table. Every
claim cites real `file:line` references; gaps (no actuator firmware, no
deep sleep yet, etc.) are called out explicitly rather than glossed over.

**Two new design specs** (approved in conversation, **not yet implemented**):
- `docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md` — pair the
  app directly against the Pi's setup hotspot with zero home WiFi ever
  configured (for sites with no ISP WiFi). Investigation found `/pair` is
  already reachable in AP mode today, gated only by a 600s timer — the real
  gap is that `/pair` hands out full MQTT credentials with **no
  authentication** beyond that timer, over **plaintext HTTP**, and mDNS/
  DNS-SD discovery is spoofable. Extended mid-session with a PIN-auth +
  5-attempt-lockout design once that gap was discussed — splits `/pair` into
  an unauthenticated existence-check (`GET /pair` → `{"found": true}`) and a
  new PIN-gated `POST /pair/confirm` that returns the real credentials.
  Applies to both the new AP-direct flow and the existing STA/home-WiFi flow.
- `docs/superpowers/specs/2026-07-20-uart-bridge-design.md` +
  `docs/superpowers/plans/2026-07-20-uart-bridge.md` — for deployments where
  physical distance between the bridge and Pi isn't a constraint, replaces
  the bridge's WiFi+MQTT+TLS uplink with a direct 3-wire GPIO UART
  connection (both ESP32 and Pi GPIO run 3.3V — no level shifter). Removes
  the router dependency and the WiFi credentials currently baked into
  `bridge_esp32.ino` firmware. 5-task implementation plan written; explicitly
  scoped to the bridge↔Pi hop only, doesn't touch the Pi's own HiveMQ
  connectivity.

**`TODO.md` (new) and `IMPROVEMENTS.md` (new)** — two root-level tracking
docs, built by reading the real source tree rather than trusting this file's
own claims (which turned out to have stale entries — see below) or the
implementation-plan checkboxes (every plan file has 0/N boxes checked
regardless of actual completion, confirmed by counting). `TODO.md` covers
what's designed-but-unbuilt or built-but-hardware-unvalidated, plus a real
gap this pass found that wasn't previously tracked anywhere: **no actuator
controller firmware exists at all** — `greenhouse/actuators/<id>/set` is
published correctly by the app and the rules engine, but nothing subscribes
to it and drives a real relay/pump/fan, only the simulator fakes it.
`IMPROVEMENTS.md` catalogs 20 code-verified findings across security,
correctness, performance, and process for code that already works but could
be better (committed WiFi/MQTT credentials needing rotation, the portal
running as root with none of its siblings' systemd sandboxing, a live-frame
memory leak, LAN camera streaming starving motion detection, etc.).

**Implemented the top `IMPROVEMENTS.md` recommendation: CI.**
`.github/workflows/ci.yml` — `pytest pi/tests/` (120 tests) and
`flutter analyze && flutter test` (~104 tests), previously only ever run
manually. The first real run caught two genuine, previously-invisible bugs:
`pi/shared/push.py` binds the `messaging` name only inside a
`try/except ImportError`, so `test_push.py` broke without `firebase-admin`
installed (fixed by installing it in CI, matching the real Pi); and 7
`deprecated_member_use` lints (`DropdownButtonFormField.value` →
`initialValue`, `Switch.activeColor` → `activeThumbColor`) that only
surfaced because CI installs current-stable Flutter rather than whatever
version was last used locally. CI is now green on `main`.

**Small security cleanup:** removed the unused MQTT-over-WebSocket listener
(port 9001) from `pi/mosquitto/mosquitto.conf` — no client has used it since
the app moved to direct TCP/TLS on 8883 (`docs/ARCHITECTURE.md`, an earlier
session). Found and removed a second orphaned reference to the same port
while at it: `pi/avahi/greenhouse-mqtt.service` advertised mDNS for it but
was **never actually installed** by `install.sh` — dead in two places, not
just one.

**Corrections made to this file's own accuracy** (the reason this session
went looking in the first place — always verify against the real tree, not
just prior notes): the 2026-07-11 entry below claimed ESP32-CAM was only
*designed*, not implemented — it was actually fully coded that same session
and just never had this file updated afterward. Also, the mesh-relay and
ESP32-CAM firmware remain genuinely uncompiled/unflashed (no toolchain in
any dev sandbox so far) — that part of the earlier claim was and still is
accurate.

**Process note:** 8 separate PRs this session (#1–#8, all merged), each
scoped to one topic, subscribed/watched to completion via
`subscribe_pr_activity` + scheduled check-ins rather than polling. The
designated working branch got restarted from `main` after every merge per
the standard convention for this setup (a merged branch can't take new
commits for a fresh PR).

---

## TL;DR of previous session (2026-07-11, ESP32-CAM design + plan)

Brainstorm → design spec → implementation plan, no implementation started. Spec: `docs/superpowers/specs/2026-07-10-esp32-cam-integration-design.md`. Plan: `docs/superpowers/plans/2026-07-11-esp32-cam-integration.md`.

**MVP scope (fully planned, ready to implement):** a single ESP32-CAM (hardware in hand, not yet flashed). LAN live view loads the camera's own MJPEG stream directly (genuinely smooth, ~10-20fps). Motion detection runs Pi-side (new `pi/scripts/cam_bridge.py`, grayscale frame-diffing on periodic snapshot POSTs from the camera) — reuses the existing FCM push pipeline (`push.send_push()`) for alerts, text-only ("Motion detected — 14:32"), with the photo fetched on tap over a new chunked MQTT request/response (no existing precedent in this codebase for binary-over-MQTT, so this is a from-scratch protocol: `{"chunk", "total", "data"}` envelopes, 3072-byte raw chunks). Event photos live on the **camera's own SD card** (not the Pi) — a deliberate choice made after discussing the tradeoff (Pi already has the bytes in-hand at detection time, so camera-side storage costs an extra fetch round-trip and makes old events unrecoverable if the camera is ever offline — user chose camera-side storage anyway, since the hardware already has an SD slot). Remote "live" view is on-demand only (never continuous background polling), relayed through the Pi at ~1-3fps over MQTT — a real two-tier quality difference from the LAN view, discussed explicitly with the user before proceeding. 7-day age-based retention, Pi-driven (camera needs no RTC).

**Phase 2 (WebRTC remote streaming) — documented, deliberately not planned:** the user explicitly asked to plan out "the real deal" for smooth remote video even though it's a bigger lift, but to decide later whether/when to build it. The spec's Phase 2 section covers the architecture (Pi runs `aiortc`, camera firmware stays unchanged, signaling rides the existing MQTT/HiveMQ bridge, media relays through a new TURN server) and flags two real open risks: a TURN relay is new public infrastructure this project doesn't otherwise need, and the Pi Zero W (no hardware video encoder) may not have the CPU headroom to encode a live WebRTC track — that bench test is the necessary first step whenever this phase is picked up, not something to assume works.

**Also corrected this session:** memory said the FCM/rule-builder branch was unmerged — it's actually already on `main` (merged since the last memory update), confirmed via `git log`/`git status` before starting new work.

---

## TL;DR of previous session (2026-07-10, FCM push + customizable alert rules)

Two features, each via brainstorm → design spec → implementation plan → subagent-driven-development → live bench-test on the real Pi Zero W + real phone.

### 1. FCM push notifications
Spec: `docs/superpowers/specs/2026-07-10-fcm-push-notifications-design.md`. Plan: `docs/superpowers/plans/2026-07-10-fcm-push-notifications.md`.

Weather/rule alerts previously only reached the phone via an in-app MQTT listener — so nothing arrived if the app was closed or backgrounded. Added `pi/shared/push.py` (Firebase Admin SDK `send_push()`, reads registered device tokens from a retained MQTT topic) called alongside every existing alert's MQTT publish in `weather.py`; the app registers/refreshes its FCM token via `FcmTokenService` and handles foreground messages through the existing `NotificationService`.

**Bench-tested live and confirmed working**: foreground, fully force-stopped, and mobile-data-only (WiFi off) all received a real push. Three real bugs found and fixed during that bench test:
- `firebase-admin`'s pip install crashed the Pi Zero W twice — `/tmp` is a ~214MB tmpfs too small for grpcio's ~190MB wheel, and pip was falling back to a multi-hour from-source build that swap-thrashed the board. Fixed with a `TMPDIR` redirect + `--prefer-binary`, baked into `install.sh`.
- The Firebase service-account key was root-owned but `greenhouse-weather.service` runs as `pi` — pushes were silently failing (caught by existing error handling, so weather.py never crashed, but zero pushes ever sent). Fixed with `chown pi:pi`/`chmod 600`, self-healing on every `install.sh` run.
- A real Riverpod bug in `app.dart`: `next.whenData(...)` inside a `ref.listenManual` callback crashed with `NoSuchMethodError` in the release build, so `registerToken()` never ran. Fixed by switching to `next.value`. Two other pre-existing `.whenData()` call sites (`control_screen.dart`, `weather_screen.dart`) may share the same latent risk — not touched, out of scope.

### 2. Customizable alert rules
Spec: `docs/superpowers/specs/2026-07-10-customizable-alert-rules-design.md`. Plan: `docs/superpowers/plans/2026-07-10-customizable-alert-rules.md`.

User wanted per-zone dry/humid duration alerts (e.g. "zone 1 soil dry for 2 days"), then explicitly asked to make rule-building fully general instead of hardcoding six new rules — "each farmer or plant wants it different." Result: any rule (zone-or-weather metric, operator, threshold, optional sustained-duration, optional actuator action, optional per-rule notification toggle) is now buildable from the app via a new rule-builder dialog (Weather → Rules tab → "Add rule"), plus a settings card to toggle the two built-in system alerts (frost forecast, daily summary) independently.

**Found while writing the plan (unrelated pre-existing bug, fixed as Task 1):** rule edits from the app never actually reached the Pi. `weather.py` has no persistent MQTT client — only CLI-based polling for a fixed set of topics — and `rules/update` was never one of them, so `publishRules()` (and the "changes sync immediately" UI copy) had silently never worked; edits only lived in the app's local state. Fixed with the same retain+poll pattern already proven for location sync (`publishRules()` gains `retain: true`, `weather.py` gains `_pull_rules_from_mqtt()`).

All 8 implementation tasks done, each independently reviewed (Task 8 and the final whole-branch review done directly by the controller after the Agent-tool review dispatch hit a session-limit error, not via a subagent). Fresh test run at final review: 79 Pi tests + 84 Flutter tests passed, `flutter analyze` clean.

**Deployed and bench-tested:** `deploy.ps1` to the bench Pi, `selftest.sh` 26/26 (one transient false-alarm on first run — services still restarting, ~20-30s to rebind port 80 — resolved by re-running once systemctl showed both services up). Updated APK built and installed on the phone (`adb install -r`, in-place update, pairing preserved), pairing window reopened via a portal restart. The interactive click-through of the rule builder itself (Add rule → confirm it lands in the Pi's `rules.json`) was queued as the next manual step but not completed by the user within this session — worth doing at the start of the next session if not already done.

**Also, in passing:** a real credential (`google-services.json` / a Firebase service-account key) briefly landed in the git-tracked tree during this session; `app/android/.gitignore` was updated to exclude both patterns before anything was pushed.

---

## Previous session (2026-07-10, repo cleanup)

Pure documentation/housekeeping pass — no code changes, nothing redeployed. Removed stale and superseded files that had accumulated across sessions:

- **Deleted (untracked scratch, not in git history):** `_dart_staging/` (a one-time bootstrap fossil from the original `flutter create`, long superseded by `app/`); three orphaned empty worktree directories left behind by earlier subagent sessions that wrote outside their prepared worktree (see the process incident noted in the 2026-07-09 entry below); `.superpowers/sdd/` (36 scratch task-briefs/reports/diffs from past subagent-driven-dev sessions).
- **Deleted (tracked, committed as `51a95c6`):** `GREENHOUSE_IOT_HANDOFF.md` (described an abandoned InfluxDB/Node-RED/Grafana/FastAPI architecture, fully superseded by this file + `docs/ARCHITECTURE.md`); `docs/ESP_NOW_BRIDGE_PROGRESS.md` (a dev-bridge test log whose own "next steps" — a USB-serial bridge — were abandoned in favor of the shipped ESP-NOW mesh relay); `INSTRUCTIONS_THEMI.md` (instructions for a different collaborator/path, no longer needed); `RUNBOOK.md` (merged into `INSTRUCTIONS.md`).
- **Updated:** `INSTRUCTIONS.md` is now the single build/flash doc — gained RUNBOOK's security notes section, the install step now points at `.\deploy.ps1` instead of the stale manual `scp` block, and hardcoded stale selftest pass-counts (16/18) were replaced with generic wording.

Committed and pushed to `main` (`51a95c6`).

---

## Previous session (2026-07-09, history custom date-range picker)

Added a **custom date-range picker** to the history chart, so users can pick an arbitrary past date range (or single day) instead of only the rolling 24h/7d/30d/90d windows. Spec: `docs/superpowers/specs/2026-07-09-history-custom-date-range-design.md`. Plan: `docs/superpowers/plans/2026-07-09-history-custom-date-range.md`.

**What changed:**
- **Backend dedup:** the query logic duplicated between `portal.py`'s HTTP `/api/history` and `recorder.py`'s MQTT `_handle_history_request` was extracted into `pi/shared/history_query.py::query_points()`, extended to accept an absolute `since`/`until` window (unix epoch seconds) alongside the existing relative `hours` window. Both transports now share one implementation.
- **App side:** `HistoryQuery`, `HistoryService.fetchPoints`, and `GreenhouseRepository.fetchHistoryViaMqtt` all gained `since`/`until` support, threaded through `historyPointsProvider`. The prediction/forecast overlay is suppressed for custom ranges (extrapolating from an arbitrary past end-date isn't a real forecast). The history screen gained a 5th "Custom…" chip that opens `showDateRangePicker`.
- Also fixed two small pre-existing bugs found in an earlier brainstorm this session: the `pressure` weather metric was silently dropped by the recorder (added to its tracked metric set), and `weather.json` was root-owned while `greenhouse-weather.service` runs as `pi` (location pushes from the app were failing silently) — now `chown pi:pi` in `install.sh`.

**Process:** brainstorm → design spec → 9-task implementation plan → subagent-driven-development. **Notable hiccup:** several early background implementer subagents (dispatched without an isolated-worktree flag) wrote files or committed directly against the `main` branch checkout instead of the prepared isolated worktree, despite being given absolute paths — caught each time, `main` was reset to its pre-session state, and the remaining tasks were implemented directly by the controller instead of via background subagents. A final whole-branch review (Opus) then caught one real Important bug: `query_points()` picked minute- vs. hour-resolution purely by requested *span*, not by how old the range was, so a short (≤48h) custom range more than ~90 days back would silently return empty even though the hourly rollup still had the data. Fixed by tightening the date-picker's `firstDate` bound to 90 days (was 730) instead of teaching the query function about retention config — the simpler, thesis-appropriate fix. Also unified a validation inconsistency between the two transports (`since`/`until` must be provided together) into `query_points()` itself.

**Verified:** pi test suite 56/56, `flutter analyze` clean, `flutter test` 61/61, release APK builds, installed + tested live on the Redmi Note 13 Pro+ over the local network (via a portal restart to reopen the pairing window), Pi redeployed via `deploy.ps1` (selftest 26/26 after a transient HiveMQ-bridge-recheck retry), and the new `since`/`until` `/api/history` params confirmed returning real data against the physical unit's recorder DB. Also discovered and fixed in passing: the sensor simulator (`pi/tools/simulator.py`) wasn't running (no real edge nodes attached either), so zone1/2/3 history was ~25h stale — restarted it (`systemd-run --unit=greenhouse-sim`) so live/history data flows again for demo purposes. This is a transient systemd-run unit, not persistent across reboot — re-run the Quick Start snippet below if it's not running in a future session.

---

## Previous session (2026-07-09, dynamic mesh relay)

Built a **dynamic multi-hop ESP-NOW mesh relay** for the sensor firmware, replacing the pure star topology (every edge node → hardcoded bridge MAC) that shipped in the earlier "multi-zone sensor mesh" commit. Full plain-language explainer: `docs/MESH_RELAY_EXPLAINED.md`. Full technical spec/plan: `docs/superpowers/specs/2026-07-09-dynamic-mesh-relay-design.md` / `docs/superpowers/plans/2026-07-09-dynamic-mesh-relay.md`.

**What it does:** sensor nodes discover neighbors via periodic broadcast beacons, pick the lowest-hop-count trusted neighbor as their "parent" (RSSI tiebreak, RPL-inspired strict-rank rule so routing loops can't form), relay each other's readings toward the bridge, use a trickle-style adaptive beacon interval (2s when unstable, backs off to 60s once settled), encrypt sensor data (not beacons — an ESP-NOW platform limitation) via a shared network-wide key, buffer readings locally if isolated, and self-heal (re-route) if a relay node dies or moves. The bridge now looks up MQTT zone by the packet's *origin* MAC (not the immediate sender, which may now be a relay), publishes sensor readings with `retain=true` (was `false` — closes an old backlog item about zone cards going blank after a broker restart), and tracks per-node online/offline status.

Built via brainstorm → design spec → implementation plan (written by a Fable 5 agent) → subagent-driven-development (4 tasks, each with implementer + reviewer subagents) → whole-branch review (Opus). Findings caught and fixed along the way: low-entropy placeholder key material (caught by an independent background security scan), a transient mutual-parent-loop edge case in the loop-safety design (mitigated with an immediate "orphan beacon" on parent loss), and a blocking MQTT-reconnect bug that would have silently stopped the bridge from beaconing during broker outages. A small off-by-default test hook (`MESH_TEST_IGNORE_BRIDGE`) was added afterward to let multi-hop be bench-tested at desk range without needing real physical distance.

**Not done yet:** the code has never been compiled (no `arduino-cli` in the dev sandbox) or run on real hardware — that's the user's next step. See `docs/MESH_RELAY_EXPLAINED.md` and the plan's Task 5 for the bench-test checklist.

---

## Previous session (2026-07-08)

Two things happened:

1. **Merged and deployed the sensor-database slice** (recorder + history API), found via the `docs/superpowers/specs/2026-07-02-sensor-database-design.md` / `docs/superpowers/plans/2026-07-02-sensor-database.md` cycle from a prior session — it had been fully implemented on a side branch but never merged. Merged, deployed to the Pi (`sudo bash install.sh` + `selftest.sh`, now 23/23), and fixed a deploy-script gotcha along the way (manual `scp` nests into a subfolder if the target dir already exists — use `deploy.ps1` instead, it `rm -rf`s first).

2. **Full history-chart feature**, built via brainstorm → design spec → implementation plan → subagent-driven development (8 tasks, each with its own implementer + reviewer subagent) → final whole-branch review → merge. Spec: `docs/superpowers/specs/2026-07-08-history-chart-enhancement-design.md`. Plan: `docs/superpowers/plans/2026-07-08-history-chart-enhancement.md`. Adds:
   - Real axes/gridlines/timestamps (was a bare unlabeled line before)
   - Metric tabs (Temp/Humidity/Soil/Light per zone; Temp/Humidity/Wind/UV/Rain for weather)
   - Time-range selector: 24h / 7d / 30d / 90d
   - A min-max shaded band around the average line
   - A dual-mode prediction overlay: real Open-Meteo forecast for temperature/rain, linear-regression trend extrapolation for everything else — with silent fallback so a flaky forecast never breaks the chart
   - New navigation: each zone-card metric chip now links to its own metric's history (was hardcoded to temperature only); the dashboard weather card got a new history icon button

Everything is committed and pushed to the private GitHub repo: https://github.com/vtsitotas/GreenHouse (`gh` CLI authed).
