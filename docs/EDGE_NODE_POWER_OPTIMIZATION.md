# Edge Node Power & Hardware Optimization Plan

> **Purpose:** Document future upgrades for making the ESP32-C3 Edge Nodes completely self-sustaining via solar and battery power. This expands on the wireless bridging tests to cover long-term field deployment.
>
> **See also:** [`SENSOR_NODE_POWER_AND_SOLAR.md`](SENSOR_NODE_POWER_AND_SOLAR.md)
> — the follow-up written against the shipped code. It re-derives the numbers
> below from the firmware's actual constants and adds the solar sizing math,
> the per-component rationale, and the battery-telemetry path to the app.

---

## 1. Core Optimization Strategies

To make the Edge Node self-sustaining without needing a massive battery or solar panel, we need to shift from an "always-on" approach to a low-power duty cycle.

### A. Deep Sleep over Delay
Instead of using `delay()` in the loop, the ESP32-C3 will use `esp_deep_sleep_start()`.
*   **Always On (Delay):** ~100mA continuous draw.
*   **Deep Sleep:** ~15µA continuous draw (CPU and Wi-Fi disabled).

### B. Hardware-Level Modifications (Super Mini Boards)
Standard development boards waste power even in deep sleep due to auxiliary components.
*   **Power LED:** The constantly lit red LED draws ~3mA. De-soldering or physically removing this LED is mandatory for ultra-low power.
*   **LDO Regulator:** Bypassing the onboard regulator by supplying a clean 3.2V - 3.3V directly eliminates quiescent current loss (~40-50µA).

### C. Zero-Component Sensor Power Switching
The DHT22 (~1.5mA) and Capacitive Soil Sensor (~5mA) constantly draw power if wired directly to VCC. 
*   **Solution:** Power the sensors directly from the ESP32-C3's GPIO pins (which can source up to 20mA). 
*   **Flow:** Set GPIO `HIGH` to power sensors -> wait 1.5s for stabilization -> read data -> set GPIO `LOW` -> enter Deep Sleep.

---

## 2. Power Consumption Analysis

Assuming a realistic duty cycle of measuring and transmitting **once every 15 minutes** (900 seconds).

### Scenario A: Unmodified Board (Sensors Always On, LED On)
*   **Active Phase (2.5s):** 86.5mA 
*   **Deep Sleep Phase (897.5s):** ~3.05mA (LED + Sensors idle draw + LDO)
*   **Average Draw:** ~3.29mA
*   **Daily Consumption:** ~79 mAh/day
*   *Feasibility:* Feasible, but requires at least 45 minutes of direct sunlight daily to break even.

### Scenario B: Optimized Board (LED Removed, Sensors Switched via GPIO)
*   **Active Phase (2.5s):** 86.5mA
*   **Deep Sleep Phase (897.5s):** ~0.055mA (55µA - only RTC and LDO quiescent)
*   **Average Draw:** ~0.29mA (290µA)
*   **Daily Consumption:** ~7 mAh/day
*   *Feasibility:* Extremely feasible. A standard 1500mAh battery could run for over **200 days in complete darkness**.

---

## 3. Recommended Hardware Stack

To achieve the optimized power profile safely in an outdoor/greenhouse environment:

| Component | Recommendation | Justification |
|---|---|---|
| **Battery** | 18650 LiFePO4 (3.2V, 1500mAh) | Connects directly to the ESP32's 3.3V pin without needing a regulator. Much safer than LiPo at extreme greenhouse temperatures. |
| **Charger** | TP5000 Solar Charging Module | Specifically designed to charge 3.6V LiFePO4 batteries safely. Prevents overcharging. |
| **Solar Panel** | 5V/1W or 6V/2W Monocrystalline | Small footprint (approx 110x60mm). Easily generates the ~7mAh daily requirement even on overcast days. |

---

> **Status update (2026-07-26):** the software side of this plan is now
> implemented as "Phase 1 leaf sleep" — see
> `docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md` (design) and
> `docs/superpowers/plans/2026-07-26-mesh-deep-sleep.md` (implementation,
> Tasks 1-5 done; Task 6 is the hardware bench checklist). The §4 flow below
> shipped in a mesh-aware form: the wake cycle also beacons, re-validates its
> remembered parent, and persists seq/buffer state in RTC memory so
> multi-hop routing and bridge de-dup survive sleep. The hardware mods in §1B
> (LED removal, LDO bypass, divider soldering) remain to be done per board.

## 4. Conceptual Software Flow for Future Expansion

When expanding the current Edge Node code (`edge_node_esp32_c3.ino`), the `loop()` function will be replaced by a single execution path in `setup()`, followed by deep sleep:

```cpp
void setup() {
  // 1. Boot up
  // 2. Turn on GPIO pins providing power to DHT22 and Soil Sensor
  // 3. Wait 1500ms for DHT22 stabilization
  // 4. Read sensor values
  // 5. Turn off GPIO pins (cut power to sensors)
  // 6. Initialize ESP-NOW
  // 7. Transmit packet to Bridge
  // 8. Go to Deep Sleep for 15 minutes
}

void loop() {
  // Never reached
}
```

This ensures the absolute minimum "time-on-air" and maximizes battery life.
