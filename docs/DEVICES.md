# Device Registry & Firmware Notes

**Last updated:** 2026-07-27

---

## Confirmed Device MACs

| Role | Board | MAC | Zone |
|---|---|---|---|
| Bridge | ESP32-C3 | `20:6E:F1:6C:75:EC` | — (bridge, nullptr) |
| Edge node | ESP32-C3 | `20:6E:F1:6C:6B:50` | `zone1` |
| Edge node | ESP32-C3 | `20:6E:F1:6C:A1:B0` | `zone2` |

> The old WROOM-32 (`88:F1:55:31:45:64`) is **retired** — removed from `TRUSTED_NODES[]`.

These MACs are the source of truth in:
`firmware/libraries/GreenhouseMesh/mesh_config.h`

---

## Firmware Assignment

| Sketch | Used by |
|---|---|
| `firmware/bridge_esp32/bridge_esp32.ino` | Bridge (6B:50) — **UART-wired to the Pi**, no WiFi/MQTT. As of 2026-07-27, wiring+power confirmed correct but zero bytes seen on the Pi's `/dev/serial0` — reflash status unconfirmed, see below. |
| `firmware/bridge_esp32_wifi_fallback/bridge_esp32_wifi_fallback.ino` | **Temporary fallback** — the pre-UART bridge firmware (WiFi + MQTT direct to the Pi), restored 2026-07-27 while the UART path is unresolved. No GPIO wiring needed, just power. Uses the same `secrets.h` as below. |
| `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` | Zone1 (75:EC) and Zone2 (A1:B0) |
| `firmware/edge_node_esp32/edge_node_esp32.ino` | Retired WROOM-32 — not in use |
| `firmware/fake_edge_node_esp32_c3/fake_edge_node_esp32_c3.ino` | Fake sensor node (random-walk data) — flash to bench-test the pipeline without real sensors, then reflash the real edge node firmware |
| `firmware/cam_esp32/cam_esp32.ino` | ESP32-CAM. As of 2026-07-27, compiles (was blocked on a missing `secrets.h` and a missing `UriBraces` include, both fixed) but not yet flashed/bench-tested. |

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
camera and the WiFi-fallback bridge both `#include <secrets.h>` and hit
"No such file or directory" without it):
```
C:\Users\themi\Documents\Arduino\libraries\GreenhouseSecrets
    → C:\Users\themi\Documents\GreenHouse\firmware\libraries\GreenhouseSecrets
```
`secrets.h` itself is gitignored and per-device — copy
`secrets.h.example` to `secrets.h` in that folder and fill in real values
(current bench WiFi is the phone hotspot `REDACTED_WIFI_SSID`; `CAM_TOKEN` must match
`/etc/greenhouse/cam_token.txt` on the Pi exactly — already set to a real
generated value as of 2026-07-27, not the install-time placeholder).

---

## Bench session, 2026-07-27

- **Bridge:** wiring confirmed correct (GPIO4→Pi pin10/RXD, GPIO5←Pi
  pin8/TXD, GND→pin6, 5V→pin2/4 for power) and the board is confirmed
  powered (LED lit) — but `sudo cat /dev/serial0` on the Pi shows **zero
  bytes** across repeated samples. Not yet confirmed whether the board has
  actually been reflashed with the current UART-based `bridge_esp32.ino`;
  that's the leading suspect. `firmware/bridge_esp32_wifi_fallback/` exists
  as a working fallback if the UART path stays stuck.
- **Camera:** never connected to `cam_bridge.py` all session
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
