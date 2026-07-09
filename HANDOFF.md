# Greenhouse IoT — Session Handoff

**Last updated:** 2026-07-09 (dynamic mesh relay session)
**Status:** ✅ Zero-touch setup, weather automation, sensor-history + chart feature, and now **dynamic multi-hop ESP-NOW mesh relay firmware** all complete and committed to `main`. Firmware is code-complete and reviewed but **not yet flashed to real hardware** — compile verification and the hardware bench test are the user's next step (no Arduino toolchain in the dev sandbox). App/Pi-side previously tested on real hardware (Pi Zero W + Redmi Note 13 Pro+); unaffected by this session.

---

## TL;DR of this session (2026-07-09)

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
# Expect 23/23. If "portal not responding" shows up, it's usually just
# mid-restart (~20s to rebind port 80) — rerun a few seconds later.

# Reopen the pairing window (it auto-expires after 600s uptime)
ssh pi@greenhouse.local "sudo systemctl restart greenhouse-portal"

# Redeploy the Pi side after code changes — from repo root, NOT manual scp:
.\deploy.ps1                          # defaults to greenhouse.local
.\deploy.ps1 -PiHost 192.168.1.54     # or target a specific IP

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
| `app/lib/screens/history/history_screen.dart` | The chart screen (fl_chart), this session's main feature |
| `app/lib/utils/history_prediction.dart` | Trend-extrapolation + forecast-overlay prediction logic |

---

## Full backlog

The project was originally scoped as 6 slices (`docs/superpowers/specs/2026-06-25-greenhouse-app-connectivity-design.md` §2). Status against that scope, plus everything found since:

**Slice status:**
- 1 App + Connectivity — ✅ done
- 2 Field Firmware (ESP-NOW mesh, WROOM bridges) — firmware done, including 2026-07-09's dynamic multi-hop relay upgrade (was pure star topology before); **not field-validated on real sensor hardware** (simulator only, and the new relay code has never even been compiled — no toolchain in the dev sandbox); BLE pairing was planned but superseded by the working mDNS/QR discovery instead
- 3 Storage + History — ✅ done, reimplemented as a local SQLite recorder (not InfluxDB) + this session's chart feature
- 4 Automation + Alerts — done differently: in-app duration-based rules (Weather screen → Rules tab, fully editable from the app) + `flutter_local_notifications`, instead of Node-RED/Telegram
- 5 Cloud Relay (multi-customer accounts, device registry, FCM push) — **not started**; current remote access is single-tenant HiveMQ Cloud + local notifications only
- 6 Field Hardening (solar/18650, IP65 enclosures, cellular fallback) — **not started**; see `docs/EDGE_NODE_POWER_OPTIMIZATION.md` for the existing plan doc

**Fixed this session (2026-07-08, later):** Mosquitto's native `connection` bridge to HiveMQ Cloud had never actually worked — 0 successful handshakes across 9 days of logs, a real Mosquitto bridge-code incompatibility with this HiveMQ cluster (not a quota/account issue). Any prior appearance of "remote access working" was the app displaying a stale retained value, not live data. Replaced with `greenhouse-hivemq-bridge.service` (small paho-mqtt forwarder script) — verified live two-way delivery, stable connection, automated round-trip check added to `selftest.sh` (now 26/26).

**Also fixed this session:** history charts now work remotely too. They previously called the Pi's HTTP `/api/history` directly, which only exists on the LAN (HiveMQ bridges MQTT, not HTTP) — so charts failed with "could not load" as soon as remote MQTT access started actually working and got tested. Added an MQTT request/response path (`greenhouse/history/request` → `greenhouse/history/response/<id>`, answered by `greenhouse-recorder`); the app now picks HTTP or MQTT based on whether it's connected local or remote. Verified end-to-end against the real HiveMQ cluster (409 real points returned).

**Bridging / firmware:**
- [ ] Bridge firmware (`firmware/bridge_esp32/bridge_esp32.ino`) publishes without `retain=true` — zone cards can show empty after a broker restart until the next packet arrives. Small, isolated fix.
- [ ] Multi-hop sensor mesh / relay bridging for far-away nodes (range extension beyond one ESP-NOW hop) — not started, tracked as a separate design track.
- [ ] Real-hardware field test of the ESP-NOW → bridge → MQTT path — everything so far has only been validated against `tools/simulator.py`.

**ML / analytics — nothing implemented yet:**
- [ ] "ML watering prediction" ("water likely needed in 2 days") — original nice-to-have, never scoped.
- [ ] Nightly export of recorder data to an external store (Postgres/Supabase) for monthly stats and weather-forecast-accuracy comparisons (predicted vs. actual, using the already-stored `greenhouse/weather/forecast` data). Deliberately deferred in the sensor-database spec — push, don't depend on pull, keep the Pi decoupled from the external service's uptime.

**Security — mostly done, two explicit exceptions:**
- ✅ Per-unit TLS certs, per-unit random OS password, captive-portal auto-popup, dead factory-provisioning code removed — all verified on real hardware.
- [ ] `/pair` and `/api/history*` are unauthenticated. Fine for LAN-only/thesis use; would need a PIN/QR/token before any public or multi-customer deployment.

**App feature gaps:**
- [ ] **Pick-a-specific-past-date view for history.** Currently only rolling windows (24h/7d/30d/90d back from now) — no fixed-calendar-date picker. Recorder data already supports it (90d minute-resolution, 2yr hourly); needs `/api/history` to accept an absolute `since`/`until` (or `date=YYYY-MM-DD`) plus a date-picker UI. Good next brainstorm→spec→plan candidate.
- [ ] **Screen-by-screen UX enhancement pass** over the rest of the app (dashboard, control, devices, pairing, settings, weather-forecast chart) — same brainstorm→spec→plan cycle as the history chart, one screen at a time.
- [ ] `pressure` weather metric is published by the simulator but silently dropped by the recorder (not in its tracked metric set) — no history for it.
- [ ] `weather.json` write-permission bug: the app's GPS/location-picker push to the Pi fails silently (service runs as `pi`, file owned `root:root`) — location/interval changes don't actually persist across a Pi reboot.
- [ ] Nice-to-haves from the original vision, all unstarted: ESP32-CAM plant time-lapse, CSV export, a smartwatch/widget glance.

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
