# Device Registry & Firmware Notes

**Last updated:** 2026-08-16 (4 sensor boards MAC-verified via
`firmware/mac_reader/`; registry below now matches `mesh_config.h` exactly —
see the 2026-08-16 bench session below)

---

## Confirmed Device MACs

| Role | Board | MAC | Zone |
|---|---|---|---|
| Bridge | ESP32-C3 | `20:6E:F1:6C:BE:80` | — (bridge, nullptr) |
| Edge node | ESP32-C3 | `20:6E:F1:6C:9D:B0` | `zone2` |
| Edge node | ESP32-C3 | `20:6E:F1:6C:6B:50` | `zone3` |
| Edge node | ESP32-C3 | `20:6E:F1:6C:75:EC` | `zone4` |

> `20:6E:F1:6C:A1:B0` (was `zone1`) pulled from `TRUSTED_NODES[]` 2026-08-16 —
> confirmed bad TX antenna (see bench session below). Not retired, just
> benched: re-add as `zone1` once the board/antenna is fixed.

> The old WROOM-32 (`88:F1:55:31:45:64`) is **retired** — removed from `TRUSTED_NODES[]`.

These MACs are the source of truth in:
`firmware/libraries/GreenhouseMesh/mesh_config.h`

---

## Firmware Assignment

| Sketch | Used by |
|---|---|
| `firmware/bridge_esp32/bridge_esp32.ino` | Bridge (BE:80) — **UART-wired to the Pi**, no WiFi/MQTT. **Confirmed working 2026-08-10** — the earlier "zero bytes on `/dev/serial0`" was a diagnostic artifact (`head -c N` buffered and got killed by `timeout` before flushing), not a dead link. Instrumented pyserial read shows reliable heartbeats. |
| `firmware/bridge_esp32_wifi_fallback/bridge_esp32_wifi_fallback.ino` | **No longer needed** — kept only as a fallback if the UART link ever regresses. Was the pre-UART bridge firmware (WiFi + MQTT direct to the Pi), restored 2026-07-27 while the UART path looked unresolved; that turned out to be a false alarm (see above). No GPIO wiring needed, just power, if it's ever used. Uses the same `secrets.h` as below. |
| `firmware\edge_node_esp32_c3\edge_node_esp32_c3.ino` | Zone2 (9D:B0), Zone3 (6B:50), Zone4 (75:EC) — zone1 (A1:B0) benched, see below |
| `firmware/edge_node_esp32/edge_node_esp32.ino` | Retired WROOM-32 — not in use |
| `firmware/fake_edge_node_esp32_c3/fake_edge_node_esp32_c3.ino` | Fake sensor node (random-walk data, same mesh/timing as the real edge firmware) — flash to a zone1-4 MAC to bench-test the pipeline before its DHT22/soil sensors are wired, then reflash the real edge node firmware |
| `firmware/mac_reader/mac_reader.ino` | Standalone utility — prints a board's MAC over Serial every 2s, no mesh/sensor code. Flash to identify an unlabeled board before assigning it a role above. |
| ~~`firmware/cam_esp32/cam_esp32.ino`~~ | **PARKED** — moved to `parked/camera/firmware/cam_esp32/`. The camera is out of the prototype; nothing flashes it and the Pi no longer runs a cam bridge. See `parked/camera/README.md`. |

---

## ⚠️ Critical: Arduino IDE Library Setup

Arduino IDE 2.x has **two** library search paths and always prefers
`Documents\Arduino\libraries\` over any custom path. If `GreenhouseMesh`
exists in both places, the one in `Documents\Arduino\libraries\` wins —
even if it's stale.

**Fix applied 2026-07-11:** The `Documents\Arduino\libraries\GreenhouseMesh`
folder has been replaced with a **directory junction** pointing to the
firmware library:

```
C:\Users\themi\Documents\Arduino\libraries\GreenhouseMesh
    → C:\Users\themi\Documents\GreenHouse\firmware\libraries\GreenhouseMesh
```

This means there is only ever **one copy** of the library. Edits to
`firmware/libraries/GreenhouseMesh/mesh_config.h` are immediately seen
by Arduino IDE — no sync needed.

**To verify the junction is intact:**
```powershell
(Get-Item "C:\Users\themi\Documents\Arduino\libraries\GreenhouseMesh").Target
# Should print: C:\Users\themi\Documents\GreenHouse\firmware\libraries\GreenhouseMesh
```

**To recreate it if ever deleted:**
```powershell
Remove-Item -Recurse -Force "C:\Users\themi\Documents\Arduino\libraries\GreenhouseMesh"
New-Item -ItemType Junction `
  -Path "C:\Users\themi\Documents\Arduino\libraries\GreenhouseMesh" `
  -Target "C:\Users\themi\Documents\GreenHouse\firmware\libraries\GreenhouseMesh"
```

**Also clear the build cache after any `mesh_config.h` edit:**
```powershell
Get-ChildItem "$env:LOCALAPPDATA\arduino\sketches" -Recurse -Directory -Filter "GreenhouseMesh" |
  Remove-Item -Recurse -Force
```

**Same junction now exists for `GreenhouseSecrets`** (added 2026-07-27 — the
WiFi-fallback bridge `#include`s `<secrets.h>` and hits "No such file or
directory" without it; the parked camera sketch did too):
```
C:\Users\themi\Documents\Arduino\libraries\GreenhouseSecrets
    → C:\Users\themi\Documents\GreenHouse\firmware\libraries\GreenhouseSecrets
```
`secrets.h` itself is gitignored and per-device — copy
`secrets.h.example` to `secrets.h` in that folder and fill in real values
(current bench WiFi is the phone hotspot `REDACTED_WIFI_SSID`). `CAM_TOKEN` is only
used by the parked camera sketch — the Pi no longer provisions
`/etc/greenhouse/cam_token.txt`.

---

## Bench session, 2026-07-27

- **Bridge:** wiring confirmed correct (GPIO4→Pi pin10/RXD, GPIO5←Pi
  pin8/TXD, GND→pin6, 5V→pin2/4 for power) and the board is confirmed
  powered (LED lit) — but `sudo cat /dev/serial0` on the Pi shows **zero
  bytes** across repeated samples. Not yet confirmed whether the board has
  actually been reflashed with the current UART-based `bridge_esp32.ino`;
  that's the leading suspect. `firmware/bridge_esp32_wifi_fallback/` exists
  as a working fallback if the UART path stays stuck.
- **Camera:** never connected to `cam_bridge.py` all session — this, plus
  the same being true across every earlier session, is why the camera was
  parked on 2026-08-02 (`parked/camera/`).
  (`greenhouse/cam/status` stuck at `online: false`) — root cause was
  `secrets.h` not existing at all, so the firmware couldn't compile. Fixed
  (see junction above), plus two real bugs in `cam_esp32.ino`: missing
  `#include <uri/UriBraces.h>`, and `PI_HOST` switched from
  `greenhouse.local` to a direct IP (`HTTPClient` doesn't resolve mDNS
  reliably on this core). Compiles now; not yet flashed.
- **Bridge MQTT account:** created a real `bridge` user in Mosquitto's
  passwd file (didn't exist before — this Pi's `/etc/greenhouse/device.json`
  predated that field) for the WiFi-fallback bridge to authenticate with.

---

## Bench session, 2026-08-10

- **Bridge UART: the "zero bytes" from 2026-07-27 was never a real fault.**
  Re-tested with an instrumented pyserial read (not `head -c N`, which
  buffers and gets killed by `timeout` before flushing — that's what read as
  silence before): 11 heartbeat lines in 20s at 115200 on `/dev/serial0` →
  `/dev/ttyS0`. `dtoverlay=disable-bt` is **not** needed on this Zero W —
  `enable_uart=1` already pins core_freq, which is the usual reason that
  overlay gets recommended.
- **Soil ADC pin moved GPIO2 → GPIO1** in `edge_node_esp32_c3.ino`. GPIO2 is
  a C3 strapping pin; the board pull-up on it pinned `analogRead()` at 4095
  permanently, presenting identically to a cold solder joint. Any C3 node
  wired against the old pin needs the AOUT wire moved **and** a reflash.
  `firmware/sensor_pin_test/` is a new standalone bench sketch (no
  WiFi/ESP-NOW/mesh) for checking DHT22 + soil joints without the mesh stack
  in the way.
- **Not yet re-confirmed against the real deployed mesh:** the bridge and
  Pi-side pipeline are proven; no edge node was observed actually delivering
  a `reading` line over the UART during this session (heartbeats only). The
  DHT22 pull-up item in "Pending" below was not directly retested against
  zone1/zone2's actual hardware this session — don't assume it's resolved
  just because `sensor_pin_test` validated a DHT22 on the bench rig.

---

## Bench session, 2026-08-16

- **All 4 sensor boards MAC-verified with `firmware/mac_reader/`** (flash,
  read Serial, repeat per board) instead of trusting this file or
  `mesh_config.h`, which had drifted apart and disagreed with each other on
  which MAC was the bridge (75:EC here vs 6B:50 in the old Firmware
  Assignment table vs BE:80 in `mesh_config.h`). `mesh_config.h` is the
  actual source of truth (nothing else compiles it in), so the bridge MAC
  (BE:80) was left untouched and this file's tables were corrected to match
  it, not the other way around.
- **Grew from 2 sensor boards to 4** (zone1-2 unchanged: A1:B0, 9D:B0; added
  zone3 6B:50, zone4 75:EC) — all four added to `TRUSTED_NODES[]` as
  `sleepy=true`. Still well under the 8-device ESP-NOW encrypted-peer cap
  (see `docs/SCALING_AND_EXPANSION_IDEAS.md`). `pi/install.sh` already seeds
  default zone1/2/3 automation rules; zone4 has none yet (not a blocker —
  add via the app's rule builder, same as any other rule).
- **Adding these nodes required reflashing the whole fleet** (bridge +
  every edge node), including boards whose own MAC didn't change — expected,
  not a bug: `TRUSTED_NODES[]` is a compile-time `static const` array
  identical in every firmware image (`mesh_config.h`'s own comment: "Adding a
  node = add its MAC/zone here and reflash the fleet"). There is currently no
  way to add a node by flashing only the bridge; `SCALING_AND_EXPANSION_IDEAS.md`
  §3 describes an unimplemented runtime-trust-store design that would get
  partway there, but even that still requires reflashing every edge node,
  just not the bridge.
- **`firmware/fake_edge_node_esp32_c3/` rewritten** to match the current
  `edge_node_esp32_c3.ino` (fixed-channel, sleepy deep-sleep cycle, RTC
  persistence, battery telemetry) — the old version predated all of that
  (router-SSID channel scan, no sleep cycle, a `light_lux` field that no
  longer exists on `SensorPacket`) and would not have compiled. Flash it to
  any zone1-4 MAC to bench-test mesh routing/relay/self-heal before its
  DHT22/soil sensors are soldered.
- **Zone1 (A1:B0) benched — bad TX antenna.** Diagnosed from the fake
  firmware's own Serial log: RTC state survived every deep sleep (genuine
  timer wakes, not power-cycling) and rssi hearing the bridge was strong and
  stable (-56 to -67 across 7 cycles), but 0/7 wake-cycle unicasts ever got a
  MAC-layer ACK — including moved to ~30cm from the bridge, which ruled out
  "just needs to be closer" and confirmed a TX-path fault (damaged/disconnected
  antenna or a bad feed solder joint) rather than a config issue. Pulled from
  `TRUSTED_NODES[]`; testing continues on zone2-4. Re-add once fixed.
- **Cleared two ghost entries from the app** via `clear_retained.sh` (both
  brokers, verified clean): zone1's now-stale `greenhouse/nodes/206EF16CA1B0/#`
  and `greenhouse/zone1/#` topics (node pulled above), plus a leftover
  `greenhouse/zone1_test/#` ghost from earlier bench testing.
- **zone2/3/4 flipped to `sleepy=false`** for a real relay test — a sleepy
  node can never be adopted as a parent (`mesh_node.h` `meshHandleBeacon`
  rejects any beacon with `MESH_FLAG_SLEEPY` set before the parent-selection
  logic even runs), so proving multi-hop relay needs at least one always-on
  node. Same `fake_edge_node_esp32_c3.ino` image, no firmware change — the
  role is looked up from `TRUSTED_NODES[]` at boot. **Revert to `true` before
  any real deployment run**: always-on draws mains-level current
  (~86mA active, no deep-sleep floor — see
  `docs/EDGE_NODE_POWER_OPTIMIZATION.md`) and won't survive on battery.
- **Real multi-hop relay confirmed working** with the field walk-test: a
  zone board taken out of the bridge's direct range adopted a nearer zone
  board as parent (rank 2, not 1) instead of going unrouted, the bridge
  logged the relayed packet, and the app's mesh map redrew the link to the
  new parent live. Currently untested: Phase 2 (synchronized wake windows,
  which would let sleepy nodes relay too) — not built, see
  `docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md` §Phase 2.

---

## Verified Functionality (2026-07-11)

| Feature | Result |
|---|---|
| Bridge beaconing on correct WiFi channel | ✅ |
| Zone1 and Zone2 direct connection to bridge (rank 1) | ✅ |
| Correct MAC → zone name mapping in MQTT topics | ✅ |
| MQTT publish with `retain=true` to Pi broker | ✅ |
| Relay chain: zone2 → zone1 → bridge → MQTT | ✅ tested |
| Packet buffering while unrouted + flush on reconnect | ✅ |
| Self-healing (3 tx failures → drop parent → rediscover) | ✅ |
| Bridge offline detection (15s no data → publishes offline) | ✅ |

---

## Pending

- **DHT22 pull-up resistor on GPIO6** — both edge nodes report
  `DHT read failed — check pull-up resistor on GPIO6`.
  Sensor data not flowing until this is wired. Soil moisture ADC works fine.
- **Real-distance relay test** — bench test confirmed relay logic works.
  Full test at actual greenhouse distances still pending.
