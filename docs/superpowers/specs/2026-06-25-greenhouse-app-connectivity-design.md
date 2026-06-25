# Greenhouse IoT — App + Connectivity Design Spec (Slice 1)

**Date:** 2026-06-25
**Author:** Bill
**Status:** Approved for implementation
**Slice:** 1 of 6 — App + Connectivity

---

## 1. Context & Scope

This spec covers **Slice 1 only**: the Flutter mobile app and the Pi-side broker/connectivity configuration that gets live sensor data to a phone, supports actuator control, and is demoable end-to-end without field hardware.

The full product is a multi-customer IoT platform for greenhouse monitoring. Slices 2–6 (ESP-NOW firmware, time-series storage, automation, cloud relay, field hardening) are defined in scope but deferred. This slice is designed so each later slice slots in cleanly.

### Out of scope for Slice 1
- ESP-NOW firmware and WROOM bridge nodes
- InfluxDB / Grafana / time-series history
- Node-RED automation rules
- Telegram / FCM push notifications
- Cloud relay backend (multi-customer accounts, device registry)
- FastAPI REST endpoints
- BLE provisioning of sensor nodes

### Demoable with
- The one working ESP32-C3 + soil moisture reading (already publishing via MQTT)
- A Python sensor simulator seeding realistic readings for all topic types
- The Pi's existing Mosquitto broker (hardened as part of this slice)

---

## 2. Full Product Decomposition (for architectural alignment)

| # | Slice | Covers |
|---|---|---|
| **1** | **App + Connectivity** ← this spec | Flutter app, MQTT-WS, Tailscale, LAN fallback, Pi hardening, simulator |
| 2 | Field Firmware | C3 deep-sleep sensors, WROOM bridges, ESP-NOW mesh, BLE pairing in app |
| 3 | Storage + History | InfluxDB ingest, FastAPI history endpoints, charts in app |
| 4 | Automation + Alerts | Node-RED rules, Telegram bot, threshold editing in app |
| 5 | Cloud Relay | Cloud MQTT broker, accounts, device registry, FCM push, multi-customer |
| 6 | Field Hardening | Solar/18650, IP65 enclosures, range tuning, cellular fallback |

---

## 3. Network Architecture

### Physical topology

```
[C3 Sensors]  ──ESP-NOW──▶  [WROOM Bridges]  ──WiFi/MQTT──▶  [Pi Server]
                                                                    │
                                                             [Mosquitto :8883/:9001]
                                                                    │
                                                       ┌────────────┴────────────┐
                                                 [Tailscale]               [LAN mDNS]
                                                       │                         │
                                                  [Flutter App — remote]  [Flutter App — local]
```

### Key architecture decisions

- **ESP-NOW sensors never join WiFi.** Only bridges and the Pi touch `INALAN_2.4G_YzPd72`. This eliminates the need to provision WiFi credentials to battery nodes.
- **One radio constraint:** WROOM bridges share one radio for ESP-NOW + WiFi. The router and all ESP-NOW peers must be locked to the **same WiFi channel** (channel pinned in bridge firmware, checked on Pi side). If the router roams channels, ESP-NOW silently breaks.
- **Piggyback Bridge count:** a 200 m grid at 30–50 m per ESP-NOW hop requires ~4–6 mains-powered WROOM bridges. Current inventory = 1; additional WROOMs needed before Slice 2.

### ESP-NOW security (Slice 2 firmware, designed now)

Each payload includes a **sequence number** and **Unix timestamp** so bridges can reject replayed packets. No shared encryption key in Slice 1 — bridging behavior is read-only to untrusted senders, hardened in Slice 2.

---

## 4. Pi-Side Configuration

### Mosquitto hardening

| Port | Protocol | Auth | Used by |
|---|---|---|---|
| 8883 | MQTT over TLS | username + password | WROOM bridges, future cloud relay |
| 9001 | MQTT over WSS (WebSocket + TLS) | username + password | Flutter app (Tailscale + LAN) |
| 1883 | MQTT plaintext | none (loopback only) | Local debug / Node-RED (loopback bind) |

- TLS: self-signed CA + server cert (generated on Pi, fingerprint distributed to app during pairing).
- Per-client credentials: one account per bridge node, one per app install (for future per-user ACLs).
- Retained messages required: bridges publish with `retain=true` so the app gets last values on connect with no database.
- LWT required: each bridge/node sets a Last Will on `greenhouse/nodes/nodeX/status` = `offline` (retained).

### mDNS (Avahi)

Pi advertises `greenhouse.local` and `_mqtt._tcp` service on port 9001. App tries mDNS first (local path); falls back to Tailscale IP.

### Tailscale

Installed on Pi. Phone installs Tailscale app, joins same tailnet. Pi's Tailscale IP is stored in app during pairing. No port-forwarding required — Tailscale is outbound-only.

### Simulator (dev tool, runs on Pi)

Python script (`tools/simulator.py`) that publishes realistic sensor data for all MQTT topics on a configurable interval. Includes:
- Randomized readings with realistic variance (temp 20–35 °C, soil 10–90 %, etc.)
- Battery voltage decay simulation
- Node online/offline LWT cycling
- Runs via systemd or manually; auto-starts in dev mode.

---

## 5. MQTT Topic Structure (Slice 1)

All topics align with the handoff doc. Topics used in Slice 1 are marked `[S1]`.

```
greenhouse/zone{N}/air/temperature     [S1] float °C, retained
greenhouse/zone{N}/air/humidity        [S1] float %, retained
greenhouse/zone{N}/soil/moisture       [S1] float %, retained
greenhouse/zone{N}/light/lux          [S1] float lux, retained
greenhouse/weather/pressure            [S1] float hPa, retained
greenhouse/actuators/{id}/set          [S1] "ON"/"OFF", not retained (command in)
greenhouse/actuators/{id}/state        [S1] "ON"/"OFF", retained (confirmed state out)
greenhouse/nodes/{id}/status           [S1] "online"/"offline", retained (LWT)
greenhouse/nodes/{id}/battery          [S1] float %, retained
```

Payload format: plain numeric string or "ON"/"OFF" string (no JSON wrapper) — keeps bridge firmware minimal. Slice 3 may add JSON envelopes via a new topic schema; old topics preserved.

---

## 6. App Architecture (Flutter)

### Target platforms
Android (primary) + iOS (secondary). One codebase. Minimum SDK: Android 8.0 / iOS 14.

### Connection layer (swappable transport)

```
GreenhouseConnection (abstract interface)
  ├─ MqttConnection (Slice 1 — MQTT-WS direct to Pi)
  └─ CloudRelayConnection (Slice 5 — cloud backend, slots in later)
```

`MqttConnection` lifecycle:
1. Try `greenhouse.local:9001` (LAN mDNS). If timeout → try Tailscale IP.
2. Subscribe to `greenhouse/#` on connect.
3. On disconnect: exponential backoff reconnect; emit `ConnectionState.offline`.
4. Expose a stream of typed `SensorReading` events to the UI.

Connection state banner: **Local** (green) / **Remote via Tailscale** (blue) / **Reconnecting** (amber) / **Offline** (red, last-known data shown).

### State management
Riverpod. One `GreenhouseRepository` per zone/node; UI providers derived from it. Retained MQTT messages pre-populate the repository on connect — no loading spinners for the happy path.

### Screen structure

```
App
└── BottomNavigation
    ├── 0: Dashboard
    ├── 1: Devices
    ├── 2: Control
    └── 3: Settings
        └── Pairing flow (first-run)
```

#### Pairing flow (first-run / Settings)
1. "Connect to your greenhouse" screen.
2. User scans a **QR code** displayed by a Pi-side script (`tools/show_qr.py`), OR taps "Enter manually."
3. QR encodes: `{ "host_lan": "greenhouse.local", "host_tailscale": "100.x.y.z", "port": 9001, "tls_fingerprint": "...", "username": "app", "password": "..." }`.
4. Credentials stored in `flutter_secure_storage`.
5. App tests connection → success screen with live reading preview → enters main app.

#### Dashboard tab
- Zone cards (one per active zone): temp, humidity, soil moisture, light lux.
- Card color: neutral (OK) / amber (threshold warn) / red (threshold critical). Thresholds hardcoded in Slice 1; editable in Slice 4.
- Live update via MQTT stream (no pull-to-refresh needed, but pull-to-force-reconnect).
- Skeletal loading state on first connect before retained values arrive.

#### Devices tab
- Node list: each row = node ID, online/offline badge (LWT), battery % + icon, last-seen timestamp.
- Offline nodes shown in red with last-known timestamp.

#### Control tab
- Actuator toggle per discovered actuator (from `greenhouse/actuators/+/state` retained messages).
- Toggle shows **Pending** (grey) until confirmed `.../state` arrives; times out to **Error** after 5 s if no confirmation.
- No optimistic update.

#### Settings tab
- Connected server info (LAN / Tailscale address, connection type).
- "Re-pair" button → pairing flow.
- App version.

### Persistence
- Pairing credentials: `flutter_secure_storage`.
- Last-known sensor values: `shared_preferences` (simple key-value cache) — shown in Offline state.
- No SQLite / no Hive in Slice 1.

---

## 7. Visual Direction

Agricultural-but-modern: clean, card-based, strong typography. Design constraints:
- Status colors must be accessible (not red/green-only — include shape/icon).
- Dark mode supported from launch.
- No heavy animations on the readings path — low-end Android devices must feel fast.

Detailed visual design (palette, typography, component library) is an implementation step.

---

## 8. Error Handling

| Scenario | Behavior |
|---|---|
| LAN unreachable | Auto-switch to Tailscale; banner updates |
| Tailscale unreachable | Offline banner; show last-known cached values |
| Actuator command no confirmation in 5s | Toggle snaps back; toast "No response from actuator" |
| TLS cert mismatch | Block connection; pairing error screen |
| QR decode fails | Fall through to manual entry |
| Simulator not running + no hardware | App shows stale retained values or skeletal state |

---

## 9. Testing

### Flutter
- **Unit tests:** `GreenhouseConnection` transport switching, retained message parsing, `SensorReading` model, connection state machine.
- **Widget tests:** Dashboard card rendering for each state (ok / warn / critical / offline), Control tab pending/confirmed/error states, Devices tab online/offline display.

### Integration
- **Simulator integration test:** start simulator, connect app, assert readings appear within 3 s and match simulator output.
- **Reconnect test:** kill simulator, assert offline banner; restart simulator, assert reconnection within backoff window.

### Pi-side
- Mosquitto config linted with `mosquitto -c mosquitto.conf --test`.
- Simulator dry-run publishes verified with `mosquitto_sub -t "greenhouse/#" -v`.

---

## 10. Open Decisions (Slice 1)

- [ ] **QR format:** use JSON as described, or a shorter encoded string? JSON is debuggable; encoded is less error-prone to scan. → Default to JSON, revisit if QR is too dense.
- [ ] **Tailscale setup in app:** app stores the Tailscale IP in pairing; the user must have the Tailscale app installed separately. → Document in README; no in-app Tailscale auth in Slice 1.
- [ ] **mDNS on Android:** Android requires `NsdManager`; reliability varies by manufacturer. → Implement mDNS but provide Tailscale IP as authoritative fallback; don't block on mDNS.

---

## 11. Hardware Needed Before Slice 2

| Item | Qty | Notes |
|---|---|---|
| ESP32-WROOM-32 DevKit | 3–5 more | Piggyback bridges for 200 m grid |
| 18650 Li-ion cells | 8 | One per C3 sensor node |
| TP4056 charger modules | 8 | Per node |
| IP65 project boxes | varies | Field enclosures (Slice 6) |
| USB 5V 2A wall adapters | 4–6 | Permanent power for bridges |

---

*End of Slice 1 spec. Next: implementation plan via writing-plans.*
