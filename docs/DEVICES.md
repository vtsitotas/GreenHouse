# Device Registry & Firmware Notes

**Last updated:** 2026-07-12

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
| `firmware/bridge_esp32/bridge_esp32.ino` | Bridge (6B:50) |
| `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` | Zone1 (75:EC) and Zone2 (A1:B0) |
| `firmware/edge_node_esp32/edge_node_esp32.ino` | Retired WROOM-32 — not in use |

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
