# UART-Wired Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bridge ESP32's WiFi+MQTT+TLS uplink to the Pi with a
direct 3-wire UART connection over GPIO pins, per
`docs/superpowers/specs/2026-07-20-uart-bridge-design.md`. Every downstream
Pi service (Mosquitto, recorder, weather, portal, HiveMQ bridge) must see
identical topics/payloads/retain behavior to today — this is a transport
swap on one hop, not a data-model change.

**Architecture:** See the design spec for the full picture. In short: bridge
firmware drops WiFi/TLS/MQTT entirely and prints newline-delimited JSON over
`Serial2`; a new Pi-side script (`serial_bridge.py`) reads that UART and
republishes to the existing loopback Mosquitto exactly as the current
WiFi-based bridge does today.

**Tech Stack:** Arduino/C++ (`ArduinoJson` for the bridge), Python 3
(`pyserial` + `paho-mqtt` for the Pi service), systemd.

## Global Constraints

- Both sides run 3.3V GPIO logic — no level shifter needed.
- Wire pinout: ESP32 TX → Pi GPIO15 (RXD), ESP32 RX → Pi GPIO14 (TXD),
  common GND. Baud rate 115200, `SERIAL_8N1`.
- Pi's primary UART (`/dev/serial0`) must have the login console disabled
  via `raspi-config` before this will work — a manual one-time OS step, not
  something `install.sh` can safely automate unattended (it changes boot
  config and could lock out serial-console access if done wrong on a unit
  someone is depending on).
- Firmware changes (Task 1) cannot be compiled or bench-tested without
  physical hardware in this environment — same situation as the mesh-relay
  and ESP32-CAM firmware before it. That task's "test cycle" is a manual
  flash-and-bench-test checklist, not automated tests.
- Follow this project's existing conventions: Python services are flat
  scripts with module-level globals (not classes), matching
  `weather.py`/`hivemq_bridge.py`; new systemd units copy the sandboxing
  block (`NoNewPrivileges`, `ProtectSystem=strict`, scoped `ReadWritePaths`)
  from `greenhouse-recorder.service`, not the unsandboxed portal service
  (see `IMPROVEMENTS.md §Α2` for why the portal is the wrong template to
  copy).
- Commit messages end with the standard `Co-Authored-By` trailer used
  throughout this repo's history.

---

## Task 1: Rewrite bridge firmware — drop WiFi/MQTT, add UART JSON output

**Files:**
- Modify: `firmware/bridge_esp32/bridge_esp32.ino`
- Modify: `firmware/libraries/GreenhouseMesh/mesh_config.h` (fixed channel constant)

**Interfaces:**
- Produces: one JSON line per event on `Serial2`, format per the design
  spec (`{"type":"reading",...}` / `{"type":"status",...}`).

- [ ] **Step 1: Add `MESH_FIXED_CHANNEL` to `mesh_config.h`**
  ```c
  #define MESH_FIXED_CHANNEL  1   // no router to scan for in a wired deployment
  ```
  Remove `MESH_RESCAN_AFTER_MS` and its usage if this is the only wiring the
  fleet will ever run (confirm with the user before deleting — if any
  wireless-bridge units are still expected to coexist, keep it behind a
  compile-time flag instead of deleting outright).

- [ ] **Step 2: Update edge node sketches to use the fixed channel**
  In `edge_node_esp32_c3.ino` and `edge_node_esp32.ino`, replace the
  `getWiFiChannel(WIFI_SSID)` scan + `esp_wifi_set_channel()` call with a
  direct `esp_wifi_set_channel(MESH_FIXED_CHANNEL, WIFI_SECOND_CHAN_NONE)`
  in `setup()`. Remove the now-unused `WIFI_SSID` `#define` and
  `getWiFiChannel()` function.

- [ ] **Step 3: Strip WiFi/MQTT from `bridge_esp32.ino`**
  Remove: `#include <WiFiClientSecure.h>`, `#include <PubSubClient.h>`,
  `WIFI_SSID`/`WIFI_PASSWORD`/`MQTT_HOST`/`MQTT_PORT`/`MQTT_USER`/`MQTT_PASS`
  `#define`s, the `WiFiClientSecure net`/`PubSubClient mqtt` globals,
  `reconnectMQTT()`, `reconnectMQTTNonBlocking()`, and all
  `mqtt.connected()`/`mqtt.loop()` calls in `loop()`.

- [ ] **Step 4: Add `ArduinoJson` and UART output**
  Add `#include <ArduinoJson.h>`. In `setup()`:
  ```c
  Serial2.begin(115200, SERIAL_8N1, RX_PIN, TX_PIN);  // pins per spec pinout
  ```
  Replace `mqttPublish(topic, payload, retain)` with a new
  `sendReading(zone, metric, value)` / `sendStatus(mac, status)` pair that
  builds a `StaticJsonDocument`, serializes it, and does
  `Serial2.println(json)`. Keep the call sites in `onDataRecv()` and
  `checkOfflineNodes()` structurally the same — only the helper they call
  changes.

- [ ] **Step 5: Update `esp_now_recv_cb`/beacon logic references**
  Confirm `meshInit()`, `onDataRecv()`'s `origin_mac` zone lookup, and
  `checkOfflineNodes()` are otherwise untouched — diff the file against
  the pre-change version to confirm no mesh logic was accidentally altered
  while removing the MQTT code around it.

- [ ] **Step 6: Manual bench test (cannot be automated)**
  Flash to real hardware. Before wiring to the Pi, connect the bridge's
  Serial2 TX to a USB-serial adapter and confirm well-formed JSON lines
  appear at 115200 baud in a terminal (e.g. `screen`/`minicom`) with a
  sensor node actually reporting.

---

## Task 2: `pi/scripts/serial_bridge.py`

**Files:**
- Create: `pi/scripts/serial_bridge.py`
- Test: `pi/tests/test_serial_bridge.py`

**Interfaces:**
- Consumes: newline-delimited JSON from `/dev/serial0`.
- Produces: MQTT publishes to `127.0.0.1:1883` matching
  `pi/scripts/recorder.py:242-252`'s `parse_topic()` expectations exactly
  (`greenhouse/<zone>/air/temperature`, `.../air/humidity`,
  `.../soil/moisture`, `greenhouse/nodes/<mac>/status`), all with
  `retain=True` — this is the correctness bar, verified in Step 4.

- [ ] **Step 1: Write the failing tests**
  Mock `serial.Serial` (matching how `test_push.py` mocks `subprocess` —
  `monkeypatch.setattr`). Cover: a well-formed `reading` line publishes the
  correct topic/payload/retain; a well-formed `status` line publishes to
  `greenhouse/nodes/<mac>/status`; a malformed JSON line is dropped without
  crashing the loop; a read timeout (empty line) is a no-op, not an error.

- [ ] **Step 2: Implement `serial_bridge.py`**
  Flat script, module-level globals, matching `hivemq_bridge.py`'s
  structure. `SERIAL_PORT = '/dev/serial0'`, `BAUD = 115200`. Reuse the
  exact zone/metric→topic mapping from `recorder.py`'s `_ZONE_METRIC_GROUPS`
  constant rather than re-deriving it independently (single source of
  truth for the topic shape).

- [ ] **Step 3: Add `pyserial` to the Pi's installed packages**
  Check whether it's available as `python3-serial` via apt (preferred,
  matches this project's apt-over-pip convention for system packages) or
  needs pip.

- [ ] **Step 4: Manual integration test against real hardware**
  With Task 1's bridge flashed and wired, run `serial_bridge.py` manually
  and confirm `mosquitto_sub -h 127.0.0.1 -p 1883 -t 'greenhouse/#' -v`
  shows output identical in shape to what the current WiFi bridge produces
  today (cross-check against `docs/technical/05-mqtt-broker.md §4`'s topic
  list).

---

## Task 3: systemd service + install.sh wiring

**Files:**
- Create: `pi/systemd/greenhouse-serial-bridge.service`
- Modify: `pi/install.sh`
- Modify: `INSTRUCTIONS.md`

- [ ] **Step 1: Write the systemd unit**
  Copy the sandboxing block from `greenhouse-recorder.service` (
  `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=read-only`,
  `ReadWritePaths=` scoped to whatever `serial_bridge.py` actually needs —
  likely nothing beyond default, since it only talks to a serial device and
  loopback MQTT). `User=pi`. Needs access to `/dev/serial0` — confirm the
  `pi` user is in the `dialout` group (standard on Raspberry Pi OS) rather
  than granting broader device permissions.

- [ ] **Step 2: Wire into `install.sh`**
  Install `python3-serial` (or pip fallback per Task 2 Step 3), copy the
  new service file, add to the `systemctl enable` list alongside the
  existing services. **Do not** remove `greenhouse-hivemq-bridge.service`
  or any WiFi-bridge-related install steps — those remain relevant for the
  Pi's own upstream connectivity (see spec Non-goals), only the
  `bridge_esp32`-to-Pi hop changes.
  Print a reminder (not an automated action, per Global Constraints) to run
  `sudo raspi-config` → Interface Options → Serial Port → disable login
  shell, keep hardware enabled → reboot, before this service will receive
  any data.

- [ ] **Step 3: Document the physical wiring + OS step in `INSTRUCTIONS.md`**
  Add a new section with the pinout diagram (from the design spec) and the
  `raspi-config` steps, placed alongside the existing ESP-NOW/flashing
  instructions.

---

## Task 4: Update stale documentation

**Files:**
- Modify: `docs/technical/04-bridge-gateway.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/technical/01-sensor-node-hardware.md` §2 (channel section)
- Modify: `docs/technical/14-network-reference.md` (bridge↔Pi row)

- [ ] **Step 1: Rewrite `04-bridge-gateway.md`**
  Replace the WiFi STA / PubSubClient / TLS sections with the UART
  transport described in the design spec — this doc becomes actively
  wrong otherwise, worse than having no doc at all.

- [ ] **Step 2: Update `ARCHITECTURE.md`'s Mermaid diagram + accuracy notes**
  The "Η γέφυρα (gateway) είναι ασύρματη" note becomes false for this
  deployment mode — either update it or add a second variant diagram,
  consistent with how §2 (first-boot pairing) already exists as a
  separate diagram alongside §1.

- [ ] **Step 3: Update the channel-scan explanation**
  `01-sensor-node-hardware.md §2` currently describes the SSID-scan trick
  as current behavior — update to describe the fixed-channel approach
  post-Task-1.

- [ ] **Step 4: Update the network reference table**
  `14-network-reference.md`'s bridge↔Mosquitto row currently lists
  `TCP/8883` MQTT-over-TLS — replace with the UART row (no OSI L3/L4 at
  all, matching how the ESP-NOW row is already documented as L2-only).

---

## Task 5: Whole-change review + bench validation

- [ ] **Step 1: Confirm zero behavior change downstream**
  With everything wired and flashed, run the existing `selftest.sh` and
  confirm it still passes — nothing in it should need to change, since
  from Mosquitto's perspective the data still just... arrives.
- [ ] **Step 2: Confirm the app sees no difference**
  Open the Flutter app against this unit; dashboard/history/nodes screens
  should behave identically to a WiFi-bridge unit — this is the ultimate
  correctness check per the spec's Goal 3.
- [ ] **Step 3: Update `HANDOFF.md` and `TODO.md`**
  Record this as a completed, hardware-validated feature (not just
  "designed," matching the standard this session set for those two files
  — verify against real code/hardware state, don't just narrate intent).
