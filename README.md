# GreenHouse

A DIY greenhouse monitoring and automation system for a thesis project:
battery-friendly ESP-NOW sensor mesh → a Raspberry Pi Zero W gateway → a
Flutter mobile app, with remote access, push alerts, and a from-scratch
security pass (TLS everywhere it can go, PIN-gated pairing, per-unit
secrets, signed/encrypted camera frames).

No cloud IoT platform, no InfluxDB/Grafana/Node-RED — the Pi Zero W runs
everything itself: a local SQLite recorder, an automation-rules engine, and
a captive-portal setup flow, with a lightweight custom bridge for remote
access over HiveMQ Cloud.

## How it fits together

```
ESP-NOW sensor mesh (battery nodes, dynamic multi-hop relay, deep sleep)
        │  UART GPIO, 3.3V, no router
        ▼
ESP32-C3 bridge → Raspberry Pi Zero W
        │  Mosquitto (local TLS + HiveMQ Cloud bridge for remote)
        │  SQLite recorder · weather/automation engine · setup portal
        ▼
Flutter app (Android) — dashboard, live mesh map, history, rules, push alerts
```

- **Sensor mesh:** ESP-NOW nodes discover neighbors, multi-hop relay toward
  the bridge, and (on battery nodes) deep-sleep between readings. See
  [`docs/MESH_RELAY_EXPLAINED.md`](docs/MESH_RELAY_EXPLAINED.md).
- **Bridge → Pi:** a direct 3-wire UART link (no WiFi/MQTT credentials on
  the bridge firmware at all) — see `INSTRUCTIONS.md`, Part 6.
- **Pi services** (`pi/`): recorder, weather/automation, setup portal, and
  the HiveMQ Cloud bridge, each a sandboxed systemd unit installed by
  `pi/install.sh`.
- **App** (`app/`): Flutter/Riverpod, MQTT+TLS with certificate pinning,
  dashboard/control/devices (with a live mesh-topology map)/weather+rules/
  history/settings screens.

Full architecture diagrams: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
A deep, OSI-layer technical reference (protocols, ports, why each piece was
chosen) lives in [`docs/technical/`](docs/technical/00-INDEX.md) — written
in Greek for the thesis writeup; everything else in this repo is English.
A friendlier, prose-style walkthrough of both the system layout and the
security work (also Greek) is
[`docs/SYSTEM_OVERVIEW_SIMPLE.md`](docs/SYSTEM_OVERVIEW_SIMPLE.md) — read
that one first.

Why the mesh caps at 8 devices, why adding a sensor needs the whole fleet
reflashed, and what it would take to lift both:
[`docs/SCALING_AND_EXPANSION_IDEAS.md`](docs/SCALING_AND_EXPANSION_IDEAS.md).
Design analysis only — **nothing in it is implemented**.

## Repo layout

| Path | What's there |
|---|---|
| `app/` | Flutter mobile app |
| `pi/` | Raspberry Pi services, installer, systemd units, tests |
| `firmware/` | ESP32/ESP32-C3 Arduino sketches (bridge + edge sensor nodes) |
| `docs/` | Architecture, technical deep-dive, design specs/plans, hardware notes |
| `parked/camera/` | The ESP32-CAM feature — built and security-hardened, but never validated on real hardware, so it's parked rather than shipped. See [`parked/camera/README.md`](parked/camera/README.md) for what it did and how to restore it. |

## Getting started

Building a unit from a blank SD card, flashing firmware, and mass-producing
more units: [`INSTRUCTIONS.md`](INSTRUCTIONS.md).

## Project status & docs

This project is built and documented session-by-session rather than with a
fixed roadmap — these track current, real state (verified against the code,
not just notes):

- [`HANDOFF.md`](HANDOFF.md) — current status and the most recent sessions'
  summaries (older sessions: [`docs/archive/HANDOFF_ARCHIVE.md`](docs/archive/HANDOFF_ARCHIVE.md))
- [`TODO.md`](TODO.md) — designed-but-unbuilt and built-but-hardware-unvalidated work
- [`IMPROVEMENTS.md`](IMPROVEMENTS.md) — things that work but could be better (Greek)
- [`SECURITY.md`](SECURITY.md) — security posture, known limits, and the credential-rotation checklist
- [`docs/SENSOR_NODE_POWER_AND_SOLAR.md`](docs/SENSOR_NODE_POWER_AND_SOLAR.md) —
  the battery node's hardware, deep-sleep power budget worked through from the
  firmware's own constants, solar sizing, and how battery % reaches the app

## Tech stack

Flutter/Dart (app) · Python (Pi services, Flask portal) · C++/Arduino
(ESP32 firmware) · Mosquitto MQTT over TLS · SQLite · systemd · GitHub
Actions CI (pytest + `flutter analyze`/`flutter test` on every push).
