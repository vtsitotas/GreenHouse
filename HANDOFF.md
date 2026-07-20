# Greenhouse IoT — Session Handoff

**Last updated:** 2026-07-20 (documentation deep-dive + design specs session)
**Status:** ✅ `main` is clean, all work merged, CI is green. This session was
**documentation and design only — no application code changed**. Corrected
two stale claims found in this file (see below) and in the ESP32-CAM entry
right below: the camera feature was actually already fully implemented back
on 2026-07-11, contrary to what this file said until now. See "Next step"
below for what's actually still open.

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

## Next step

No single obvious next step was chosen this session — it was documentation/
design only. Candidates, roughly in order of how self-contained they are
(see `TODO.md` for the full picture):

1. **Direct-to-Pi pairing + PIN auth** (`docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md`)
   — approved in conversation, no implementation plan written yet. Would
   need one written (mirroring the UART bridge spec's task-list format)
   before subagent-driven-development could start.
2. **UART-wired bridge** (`docs/superpowers/specs/2026-07-20-uart-bridge-design.md`
   + `docs/superpowers/plans/2026-07-20-uart-bridge.md`) — spec and 5-task
   plan both already written, ready to implement. Firmware task (Task 1)
   can't be bench-tested without physical hardware, same caveat as
   mesh-relay/ESP32-CAM before it.
3. **Real-hardware field validation** of the mesh relay and ESP32-CAM
   firmware — both are fully coded and have never been compiled or flashed
   (no `arduino-cli` toolchain in any dev sandbox so far). This blocks
   several `TODO.md` items from moving out of "designed, unvalidated."
4. Work through `IMPROVEMENTS.md`'s remaining findings — Δ1 (CI) and Α6
   (unused port 9001) are already done from this session; Α1 (rotate
   committed WiFi/HiveMQ credentials) is probably the next highest-value one
   given it's a live, real exposure.

Ask the user which before picking one — none was prioritized explicitly.

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

---

## Quick Start (next session)

```bash
# SSH to the master Pi — passwordless (admin key authorized)
ssh pi@greenhouse.local
# DHCP IP varies by session; OS password is per-unit random (see
# /boot/firmware/INITIAL_PASSWORD.txt) — use the SSH key, not a password.

# Verify the Pi
ssh pi@greenhouse.local "sudo bash /home/pi/greenhouse/scripts/selftest.sh"
# Expect 26/26 (as of 2026-07-09). If "portal not responding" shows up, it's
# usually just mid-restart (~20s to rebind port 80) — rerun a few seconds later.

# Reopen the pairing window (it auto-expires after 600s uptime)
ssh pi@greenhouse.local "sudo systemctl restart greenhouse-portal"

# Redeploy the Pi side after code changes — from repo root, NOT manual scp:
.\deploy.ps1                          # defaults to greenhouse.local
.\deploy.ps1 -PiHost 192.168.1.54     # or target a specific IP

# No real sensors attached? The simulator isn't a persistent service --
# restart it if zone1/2/3 history looks stale:
ssh pi@greenhouse.local "sudo systemd-run --collect --unit=greenhouse-sim bash -c 'python3 /home/pi/greenhouse/tools/simulator.py --interval 10'"

# Build + install the app (phone via USB)
export PATH="$PATH:/c/Users/billy/flutter/bin"   # Git Bash
cd app
flutter pub get
flutter build apk --release
flutter install -d <device-id>   # `flutter devices` to list; approve the
                                  # install prompt ON THE PHONE when it appears
```

**No real sensors attached right now?** Run the simulator on the Pi to generate fake zone1/2/3 sensor data so the recorder/history chart has something to show:
```bash
ssh pi@greenhouse.local "sudo systemd-run --collect --unit=greenhouse-sim bash -c 'python3 /home/pi/greenhouse/tools/simulator.py --interval 10'"
```

---

## Architecture (current)

```
┌─────────── FIELD / GREENHOUSE ───────────┐
│  ESP-NOW sensor nodes, dynamic multi-hop  │  (firmware done, not yet
│    mesh relay → ESP32 bridge              │   field-tested on real HW —
│     → MQTT publish to Pi                  │   see docs/MESH_RELAY_EXPLAINED.md)
└──────────────────┬─────────────────────────┘
                    │ MQTT (loopback 1883, internal services)
┌───────────────────▼───────────────────────────────┐
│  Raspberry Pi Zero W                              │
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
│  Dashboard, Control, Devices, Weather+Rules,       │
│  History (chart, this session's feature), Settings │
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

---

## Full backlog

The project was originally scoped as 6 slices (`docs/superpowers/specs/2026-06-25-greenhouse-app-connectivity-design.md` §2). Status against that scope, plus everything found since:

**Slice status:**
- 1 App + Connectivity — ✅ done
- 2 Field Firmware (ESP-NOW mesh, WROOM bridges) — firmware done, including 2026-07-09's dynamic multi-hop relay upgrade (was pure star topology before); **not field-validated on real sensor hardware** (simulator only, and the new relay code has never even been compiled — no toolchain in the dev sandbox); BLE pairing was planned but superseded by the working mDNS/QR discovery instead
- 3 Storage + History — ✅ done, reimplemented as a local SQLite recorder (not InfluxDB) + this session's chart feature
- 4 Automation + Alerts — ✅ done, and this session made it fully customizable: in-app rule builder (any zone/metric/operator/threshold/duration/action, Weather screen → Rules tab → "Add rule") instead of six hardcoded thresholds, plus a real fix for rule edits never having reached the Pi (see this session's TL;DR above)
- 5 Cloud Relay (multi-customer accounts, device registry, FCM push) — **partially done this session**: FCM push notifications now work (app closed/backgrounded still gets real alerts) via `pi/shared/push.py` + a retained-token registry topic; multi-customer accounts/device registry still **not started** — current remote access is still single-tenant HiveMQ Cloud
- 6 Field Hardening (solar/18650, IP65 enclosures, cellular fallback) — **not started**; see `docs/EDGE_NODE_POWER_OPTIMIZATION.md` for the existing plan doc

**Fixed this session (2026-07-08, later):** Mosquitto's native `connection` bridge to HiveMQ Cloud had never actually worked — 0 successful handshakes across 9 days of logs, a real Mosquitto bridge-code incompatibility with this HiveMQ cluster (not a quota/account issue). Any prior appearance of "remote access working" was the app displaying a stale retained value, not live data. Replaced with `greenhouse-hivemq-bridge.service` (small paho-mqtt forwarder script) — verified live two-way delivery, stable connection, automated round-trip check added to `selftest.sh` (now 26/26).

**Also fixed this session:** history charts now work remotely too. They previously called the Pi's HTTP `/api/history` directly, which only exists on the LAN (HiveMQ bridges MQTT, not HTTP) — so charts failed with "could not load" as soon as remote MQTT access started actually working and got tested. Added an MQTT request/response path (`greenhouse/history/request` → `greenhouse/history/response/<id>`, answered by `greenhouse-recorder`); the app now picks HTTP or MQTT based on whether it's connected local or remote. Verified end-to-end against the real HiveMQ cluster (409 real points returned).

**Bridging / firmware:**
- [x] ~~Bridge firmware publishes without `retain=true`~~ — fixed as part of the
      2026-07-09 dynamic mesh relay session (`bridge_esp32.ino`'s
      `mqttPublish()` now always passes `retain=true`). This checkbox was
      never updated at the time; corrected 2026-07-20 after verifying against
      the real firmware.
- [x] ~~Multi-hop sensor mesh / relay bridging for far-away nodes~~ — built
      2026-07-09 (dynamic mesh relay, see `docs/MESH_RELAY_EXPLAINED.md`).
      Duplicate of the note already correctly reflected in Slice 2 above;
      this line just hadn't been updated to match.
- [ ] Real-hardware field test of the ESP-NOW → bridge → MQTT path —
      everything so far has only been validated against `tools/simulator.py`.
      Still true as of 2026-07-20.
- [ ] **New, no design yet:** if the UART-wired bridge
      (`docs/superpowers/specs/2026-07-20-uart-bridge-design.md`) gets built,
      the ESP-NOW mesh's channel-follows-the-router-SSID trick needs to
      switch to a fixed channel (covered in that spec's Goal 4 — not a
      separate item, just flagging the dependency here too).

**ML / analytics — nothing implemented yet:**
- [ ] "ML watering prediction" ("water likely needed in 2 days") — original nice-to-have, never scoped.
- [ ] Nightly export of recorder data to an external store (Postgres/Supabase) for monthly stats and weather-forecast-accuracy comparisons (predicted vs. actual, using the already-stored `greenhouse/weather/forecast` data). Deliberately deferred in the sensor-database spec — push, don't depend on pull, keep the Pi decoupled from the external service's uptime.

**Security — mostly done, a few explicit exceptions:**
- ✅ Per-unit TLS certs, per-unit random OS password, captive-portal auto-popup, dead factory-provisioning code removed — all verified on real hardware.
- [ ] `/pair` and `/api/history*` are unauthenticated. **Design now exists**
      (`docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md`,
      updated 2026-07-20 with a PIN + 5-attempt-lockout mechanism) but is
      **not implemented yet** — no implementation plan written either.
- [x] ~~Unused MQTT WebSocket listener (port 9001)~~ — removed 2026-07-20
      from `pi/mosquitto/mosquitto.conf`, plus a second orphaned reference
      in `pi/avahi/greenhouse-mqtt.service` that was never even installed.
- [ ] Real committed secrets in tracked firmware/install files (bridge and
      camera WiFi passwords, MQTT password, HiveMQ Cloud credentials) —
      found 2026-07-20 (`IMPROVEMENTS.md §Α1`), not yet rotated or moved out
      of git history.

**App feature gaps:**
- [x] ~~Pick-a-specific-past-date view for history~~ — done 2026-07-09. A "Custom…" chip on the history screen opens a date-range picker (bounded to 90 days back, matching minute-resolution retention); both `/api/history` (HTTP) and the MQTT history request now accept absolute `since`/`until`. See this session's TL;DR above.
- [ ] **Screen-by-screen UX enhancement pass** over the rest of the app (dashboard, control, devices, pairing, settings, weather-forecast chart) — same brainstorm→spec→plan cycle as the history chart, one screen at a time.
- [x] ~~`pressure` weather metric silently dropped by the recorder~~ — fixed 2026-07-09 (added to `_WEATHER_METRICS`). Note: `weather.py` (the real Open-Meteo service) still doesn't publish real pressure data, only `simulator.py` does — recording it is now correct, but there's no real pressure source yet.
- [x] ~~`weather.json` write-permission bug~~ — fixed 2026-07-09 (`chown pi:pi` in `install.sh`).
- [x] ~~Alerts don't arrive when the app is closed~~ — fixed 2026-07-10 via FCM push notifications. See this session's TL;DR above.
- [x] ~~Hardcoded frost/daily-summary-only alerts, no per-sensor dry/humid duration rules~~ — fixed 2026-07-10 via the customizable rule builder (any zone/metric/operator/threshold/duration/action). See this session's TL;DR above.
- [x] ~~Rule edits from the app didn't actually reach the Pi~~ — fixed 2026-07-10 (pre-existing bug found while writing the alert-rules plan; `publishRules()` now uses the retain+poll pattern proven for location sync).
- [x] ~~ESP32-CAM live view + motion alerts~~ — **actually already fully
      implemented**, contrary to what this line said until 2026-07-20. The
      code (`firmware/cam_esp32/cam_esp32.ino`, `pi/scripts/cam_bridge.py`,
      `pi/shared/motion.py`/`cam_store.py`, app-side `camera_screen.dart` +
      tests) was written in or shortly after the 2026-07-11 session but this
      file was never updated to reflect it — a real "verify against the code,
      not just prior notes" lesson. Still open: firmware has never been
      flashed to physical hardware or bench-tested (same caveat as the mesh
      relay). WebRTC remote streaming (Phase 2) remains genuinely not
      planned/started.
- [ ] Other nice-to-haves from the original vision, all unstarted: CSV export, a smartwatch/widget glance.

**Housekeeping:**
- [ ] `debugPrint()` calls in `mqtt_connection.dart` / `connection_provider.dart` — remove before any demo.
- [ ] `WEATHER_INTERVAL` is still set to its 30s debug value on `greenhouse-weather.service` — reset to a production-appropriate interval before field deployment.
- [ ] Golden image + **clone path still unproven on a 2nd physical unit**.
- [ ] iOS completely untested (Android only; thesis device is a Redmi Note 13 Pro+).
- [ ] Minor: no direct test exercises the forecast-timeout/failure fallback path in `historyWithPredictionProvider` (verified correct by code review, just not test-proven).

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
