# UART-Wired Bridge (replace WiFi bridge uplink) — Design Spec

**Date:** 2026-07-20
**Status:** Proposed — needs approval before implementation planning

## Background

Today the bridge (`firmware/bridge_esp32/bridge_esp32.ino`) is deliberately
**wireless**: it joins the home WiFi as a client and speaks MQTT-over-TLS to
the Pi's Mosquitto broker on port 8883 (see
`docs/technical/04-bridge-gateway.md`). This lets the bridge physically sit
inside the greenhouse — close to the sensor mesh, one ESP-NOW hop away —
while the Pi lives wherever is convenient (e.g. inside the house).

For deployments where the bridge and the Pi will always be physically close
together (confirmed acceptable for this use case — physical distance between
bridge and Pi is not a constraint here), the WiFi hop is unnecessary
complexity: it requires a home router, a full TLS/MQTT client stack on the
ESP32, and per-unit WiFi credentials baked into firmware
(`docs/technical/10-security.md §1`, already flagged in `IMPROVEMENTS.md §Α1`
as a real committed-secret problem). A direct wired UART connection between
the bridge's GPIO pins and the Pi's GPIO pins removes all of that: no router
dependency, a much smaller bridge firmware, and no WiFi credentials to leak.

This was considered once before in this project's history and shelved —
`docs/ARCHITECTURE.md` notes "Η σύνδεση USB serial στο Pi ήταν παλιό σχέδιο
και δεν υπάρχει" (a USB-serial-to-Pi connection was an old plan and doesn't
exist). This spec revives that idea specifically as **raw GPIO UART**, not
USB, and scopes it narrowly to the bridge↔Pi hop only.

## Goals

1. Replace the bridge's WiFi+MQTT+TLS uplink with a 3-wire UART connection
   (TX/RX/GND) directly between the bridge's GPIO pins and the Pi's GPIO
   pins — no router, no WiFi credentials on the bridge.
2. Preserve every existing behavior the bridge currently provides: per-zone
   MQTT topic publishing with `retain=true`, origin-MAC-based zone lookup,
   and the `greenhouse/nodes/<mac>/status` online/offline tracking
   (`docs/technical/03-mesh-routing.md §9`).
3. Keep the rest of the Pi pipeline (Mosquitto, recorder, weather, portal,
   HiveMQ bridge) **completely unaware** of this change — the new Pi-side
   service republishes to the existing loopback broker exactly as the
   current bridge firmware does today, so nothing downstream needs to change.
4. Remove the ESP-NOW mesh's dependency on a home router's WiFi channel
   (`docs/technical/01-sensor-node-hardware.md §2`) — replace the
   scan-the-router's-channel trick with a fixed, hardcoded channel, since a
   router may not exist at all in this deployment.
5. Drastically simplify the bridge firmware by removing the WiFi/TLS/MQTT
   client stack entirely.

## Non-goals

- **Removing the Pi's own internet/remote access.** This spec only replaces
  the bridge↔Pi hop. Whether the Pi itself has any internet path (Ethernet,
  WiFi client, cellular) for the HiveMQ Cloud bridge
  (`docs/technical/08-cloud-bridge.md`) is a separate, orthogonal decision —
  already covered for the no-ISP-WiFi case by
  `docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md`. Nothing
  here assumes or requires the Pi to have internet.
- **USB-serial instead of raw GPIO UART.** Plugging the bridge's USB port
  into the Pi (via its onboard CP2102/CH340 programming-USB chip) would be
  simpler to wire (one USB cable, no bare GPIO wires) and was actually the
  project's original abandoned idea. Explicitly not what's being built here
  — the user confirmed raw GPIO UART specifically. Worth reconsidering later
  if wiring proves fiddly in practice, but out of scope for this spec.
- **Changing anything about the sensor↔bridge ESP-NOW mesh's routing
  algorithm.** Rank/beacon/trickle logic (`docs/technical/03-mesh-routing.md`)
  is unchanged — only how the bridge gets data to the Pi changes, and the
  fixed-channel tweak in Goal 4.
- **Physical distance / cabling robustness beyond a few meters.** Raw UART
  over unshielded wire is reliable only over short runs; this spec assumes
  the bridge sits within easy cabling distance of the Pi (confirmed
  acceptable). No RS-485 or other long-distance serial standard considered.

## Architecture

### Physical connection

```
ESP32 (bridge)  GPIO_TX  ──────────────►  Pi GPIO15 (RXD)
ESP32 (bridge)  GPIO_RX  ◄──────────────  Pi GPIO14 (TXD)
ESP32 (bridge)  GND      ───────────────  Pi GND
```

Both sides run GPIO logic at **3.3V** — the ESP32 and the Raspberry Pi's
GPIO header are voltage-compatible directly, no level shifter needed (unlike
a 5V Arduino Uno). Power: the bridge can optionally be powered from the Pi's
5V or 3.3V GPIO pin instead of a separate USB supply, simplifying the
physical build to one enclosure/one power source — left as an implementation
choice, not required.

### Pi-side UART must be freed from the Linux serial console

Raspberry Pi OS by default may route the primary UART (`/dev/serial0`) to a
login console (`raspi-config` → Interface Options → Serial Port → disable
"login shell over serial", keep "serial port hardware enabled"). This is a
one-time OS configuration step, not application code — document it in
`INSTRUCTIONS.md`, and have `install.sh`/a new setup script check for it.

### Wire protocol — newline-delimited JSON, not binary

Matches this project's existing preference for human-readable payloads over
MQTT (every existing topic is a plain string or JSON, never packed binary —
`docs/technical/05-mqtt-broker.md §4`) rather than inventing a new binary
framing. One JSON object per line, terminated by `\n`:

```json
{"type":"reading","zone":"zone1","metric":"air_temperature","value":23.4}
{"type":"reading","zone":"zone1","metric":"air_humidity","value":61.0}
{"type":"reading","zone":"zone1","metric":"soil_moisture","value":42.0}
{"type":"status","mac":"206EF16CA1B0","status":"online"}
{"type":"status","mac":"206EF16CA1B0","status":"offline"}
```

This is a straightforward serialization of exactly what
`bridge_esp32.ino`'s `mqttPublish()` calls already send today
(`docs/technical/04-bridge-gateway.md §6`) — the mapping from mesh packet to
message is unchanged, only the transport of that message changes from
"publish over TLS/MQTT" to "print one JSON line over UART."

### Bridge firmware changes

- Remove entirely: `WiFiClientSecure`, `PubSubClient`, `WIFI_SSID`/
  `WIFI_PASSWORD`/`MQTT_HOST`/`MQTT_PORT`/`MQTT_USER`/`MQTT_PASS` `#define`s,
  `reconnectMQTT()`/`reconnectMQTTNonBlocking()`.
- Add: `Serial2.begin(115200, SERIAL_8N1, RX_PIN, TX_PIN)` in `setup()`, and
  a small `sendLine(const char* json)` helper replacing `mqttPublish()`.
- Add: `ArduinoJson` library dependency (lightweight, well-established,
  simplest way to build the JSON lines without hand-rolled string
  concatenation and its escaping bugs).
- Unchanged: all mesh/ESP-NOW logic — `meshInit()`, `onDataRecv()`,
  `checkOfflineNodes()`, the `origin_mac`-based zone lookup. Only the
  *delivery* of the resulting event changes.
- Rank-0 beacon logic: unchanged, still fixed 2s interval — nothing here
  affects the bridge's role as the mesh's anchor.

### New Pi-side service: `pi/scripts/serial_bridge.py`

Mirrors the structure of `pi/scripts/hivemq_bridge.py` (flat script,
module-level state, its own systemd unit) rather than introducing a new
pattern:

```python
import json, serial
import paho.mqtt.client as mqtt

SERIAL_PORT = '/dev/serial0'
BAUD = 115200
MQTT_HOST, MQTT_PORT = '127.0.0.1', 1883

def run():
    client = mqtt.Client(...)
    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
    while True:
        line = ser.readline()
        if not line:
            continue  # timeout, keep looping — not an error
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue  # malformed line (noise, partial write) — drop and continue
        if msg['type'] == 'reading':
            topic = f"greenhouse/{msg['zone']}/.../{msg['metric']}"  # exact mapping TBD in plan
            client.publish(topic, str(msg['value']), retain=True)
        elif msg['type'] == 'status':
            client.publish(f"greenhouse/nodes/{msg['mac']}/status", msg['status'], retain=True)
```

New systemd unit `greenhouse-serial-bridge.service`, same sandboxing
directives as its siblings (`NoNewPrivileges`, `ProtectSystem=strict`,
`ReadWritePaths` scoped to what it needs) — see `IMPROVEMENTS.md §Α2` for
why this matters (the portal's lack of this hardening was flagged there;
don't repeat that omission on a brand-new service).

### ESP-NOW mesh channel — fixed instead of router-derived

`edge_node_esp32_c3.ino`'s `getWiFiChannel()` scan-by-SSID trick
(`docs/technical/01-sensor-node-hardware.md §2`) assumes a router exists to
scan for. With no WiFi anywhere in this deployment, replace it with a
hardcoded constant, e.g. `MESH_FIXED_CHANNEL 1` in `mesh_config.h`, set on
every node (edge nodes and bridge alike) at `esp_wifi_set_channel()` time
instead of deriving it from a scan. Simpler than today's mechanism, and
removes the `MESH_RESCAN_AFTER_MS` re-scan-on-timeout logic entirely (no
router to change channels on you).

## Fault Handling & Reliability

| Fault | Behavior |
|---|---|
| Pi reboots while bridge is mid-transmission | Bridge keeps trying `Serial2.print()` regardless (UART has no "connection" state to lose) — `serial_bridge.py` simply isn't there to read it; next lines are dropped until the service restarts and reopens the port. No buffering across a Pi outage (same accepted trade-off as the WiFi bridge losing messages during a broker outage today). |
| Cable unplugged / bridge power-cycled | `serial.Serial()` read times out (`timeout=1`), loop just keeps polling — `pyserial` auto-recovers once the port is valid again. |
| Malformed/partial JSON line (e.g. bridge reboot mid-line) | Dropped silently (`json.JSONDecodeError` caught), loop continues — one lost event, not a crashed service. |
| Two bridges accidentally wired to the same Pi UART | Out of scope — a UART link is inherently point-to-point (unlike the current WiFi/MQTT model, which already supports exactly one bridge by convention, not by protocol enforcement). Physical wiring mistake, not a software concern. |

## Testing / Validation

Same caveat as the mesh relay and ESP32-CAM firmware
(`docs/technical/03-mesh-routing.md`, `TODO.md §2`): the bridge firmware
side cannot be compiled or bench-tested without physical hardware. Bench
plan:

1. Wire bridge↔Pi per the pinout above; confirm `raspi-config` UART setting
   frees `/dev/serial0` from the console (`ls -l /dev/serial0`, check no
   `getty` is attached).
2. Flash the rewritten bridge firmware; confirm `serial_bridge.py` receives
   and correctly parses JSON lines (manual `cat /dev/serial0` first, before
   wiring up the real parser, to eyeball the raw stream).
3. Confirm `mosquitto_sub -t 'greenhouse/#' -v` on the Pi shows the same
   topics/payloads/retain behavior as the current WiFi bridge produces
   today — this is the correctness bar: downstream (recorder, app) must not
   notice any difference.
4. Power-cycle the bridge mid-test; confirm `serial_bridge.py` recovers
   without needing its own restart.
5. Confirm the fixed-channel mesh change doesn't break existing edge-node
   pairing (same bench plan as the original mesh-relay spec, Task 5).

The Pi-side `serial_bridge.py` **can** get real automated tests, following
this project's existing pattern (`pi/tests/test_*.py`, mock the `serial.Serial`
object the way `test_push.py` mocks `subprocess`) — unlike the firmware, this
part doesn't need physical hardware to verify.

## Files Touched

| File | Change |
|---|---|
| `firmware/bridge_esp32/bridge_esp32.ino` | Remove WiFi/TLS/MQTT client entirely; add UART JSON-line output |
| `firmware/libraries/GreenhouseMesh/mesh_config.h` | Add `MESH_FIXED_CHANNEL`; remove router-scan-related constants if no longer needed |
| `firmware/edge_node_esp32_c3.ino`, `edge_node_esp32.ino` | Replace `getWiFiChannel()` scan with the fixed channel constant |
| `pi/scripts/serial_bridge.py` (new) | Reads UART, republishes to loopback Mosquitto |
| `pi/systemd/greenhouse-serial-bridge.service` (new) | systemd unit, sandboxed like its siblings |
| `pi/tests/test_serial_bridge.py` (new) | Mocked-serial unit tests |
| `pi/install.sh` | Install `pyserial`, enable the new service, check/warn about UART console setting |
| `INSTRUCTIONS.md` | Document the `raspi-config` UART step and the physical wiring diagram |
| `docs/technical/04-bridge-gateway.md` | Rewrite to describe UART transport instead of WiFi/MQTT (this doc becomes inaccurate otherwise) |

## Placeholder / Consistency Check

One open detail deferred to implementation planning: the exact topic-name
mapping inside `serial_bridge.py` (the `topic = f"greenhouse/{zone}/..."`
line above is illustrative — needs the real `air/temperature` vs
`soil/moisture` structure matching `parse_topic()` in
`pi/scripts/recorder.py:242-252` exactly, reusing that existing convention
rather than inventing a new one).
