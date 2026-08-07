# Sensor Node — Hardware, Power Budget & Solar Self-Sufficiency

> **What this is:** the complete picture for the battery-powered sensor node —
> what hardware is on it and what each part does, how the deep-sleep duty cycle
> works, the power math worked through from the firmware's own constants, why a
> small solar panel makes it self-sustaining, and how battery state gets from
> the ADC pin to the app screen.
>
> **Relationship to the other power doc:**
> [`EDGE_NODE_POWER_OPTIMIZATION.md`](EDGE_NODE_POWER_OPTIMIZATION.md) is the
> original plan that set the strategy and the target numbers. This document is
> the follow-up written **against the shipped code** — every constant below is
> quoted from a real file, and it extends the original with the solar sizing
> math, the component rationale, and the battery-telemetry path to the app.
>
> **Status caveat, up front:** the current/duration figures (86.5 mA active,
> 55 µA sleep, 2.5 s awake) are **design estimates inherited from the original
> plan, not bench measurements.** The firmware side is written and merged; the
> hardware mods it depends on (LED removal, LDO bypass, divider soldering)
> remain to be done per board, and the sleep-current claim is unverified on
> real hardware. See §7.

---

## 1. What the prototype is made of

The fleet has two edge nodes with **different power roles**, set in
`firmware/libraries/GreenhouseMesh/mesh_config.h`'s `TRUSTED_NODES[]`:

| MAC | Zone | Chip | `sleepy` | Power role |
|---|---|---|---|---|
| `20:6E:F1:6C:6B:50` | — (bridge) | ESP32-C3 | `false` | Mains, via the Pi's USB/UART |
| `20:6E:F1:6C:A1:B0` | zone1 | ESP32-C3 Super Mini | **`true`** | **Battery + solar — the self-sustaining node** |
| `88:F1:55:31:45:64` | zone2 | ESP32 WROOM-32 | `false` | Always-on, must be mains |

Only **zone1** runs the deep-sleep path (`runSleepyCycle()`). Everything below
describes that node.

### Components on the zone1 board

| Part | Pin | What it does | Power detail |
|---|---|---|---|
| **ESP32-C3 Super Mini** | — | MCU + 2.4 GHz radio. ESP-NOW on fixed channel 1 (`MESH_FIXED_CHANNEL`), never associates to WiFi | ~86.5 mA awake, ~15 µA deep sleep (chip alone) |
| **DHT22 / AM2302** | data GPIO6, **power GPIO5** | Air temperature + humidity | ~1.5 mA, only while GPIO5 is HIGH |
| **Capacitive soil sensor** | data GPIO2 (ADC1_CH2), **power GPIO4** | Soil moisture; calibrated `SOIL_DRY_VAL=3163` / `SOIL_WET_VAL=1529` | ~5 mA, only while GPIO4 is HIGH |
| **2 × 220 kΩ divider** | GPIO3 (ADC1_CH3) | Halves battery voltage into ADC range so the node measures its own cell | **~7.5 µA, always on** (accepted trade-off — avoids a high-side switch part) |
| **LiFePO4 18650, 3.2 V ~1500 mAh** | direct to 3V3 | Energy buffer **and** the reason no regulator is needed | see §5 |
| **TP5000** | between panel and cell | LiFePO4-safe solar charge controller | see §5 |
| **6 V / 2 W mono panel** | into TP5000 | Energy source | see §5 |

### The key architectural trick

The sensors are **not** wired to VCC. They're wired to **GPIO pins used as
switched power rails**. The ESP32-C3 sources ~20 mA per pin, comfortably
covering DHT22 (1.5 mA) + soil sensor (5 mA) together. Between readings the
sensors draw *literally zero* — no MOSFET, no load switch, no extra parts.

---

## 2. The optimized power plan

### A. Deep sleep instead of `delay()`

The always-on `loop()` path sits at ~100 mA continuously. The sleepy path never
returns — its last statement is always `esp_deep_sleep_start()`. From
`goToSleep()` in `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino`:

```cpp
uint64_t sleepMs = (MESH_SLEEP_INTERVAL_MS > awake + MESH_MIN_SLEEP_MS)
                     ? (MESH_SLEEP_INTERVAL_MS - awake) : MESH_MIN_SLEEP_MS;
```

It subtracts however long the wake actually took, so the **cycle period stays
exactly 15 minutes** whether that wake was fast or hit a retry. Drift doesn't
accumulate across wakes.

### B. GPIO-switched sensor power, overlapped with radio bring-up

```cpp
digitalWrite(SOIL_PWR_PIN, HIGH);   // sensors on
digitalWrite(DHT_PWR_PIN,  HIGH);
uint32_t warmupStart = millis();     // <- warm-up clock starts HERE
... esp_now_init(); meshInit(0); meshSendBeaconNow(...);   // radio comes up DURING warm-up
while (millis() - warmupStart < SENSOR_WARMUP_MS && millis() < deadline) delay(10);
```

The 2 s DHT22 warm-up runs **concurrently** with radio bring-up instead of
after it. That overlap is worth ~1–1.5 s of active time on every wake.

`goToSleep()` also forces both PWR pins LOW defensively, so no code path can
leave a sensor energised through a 15-minute sleep.

### C. Cutting every avoidable millisecond

```cpp
if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER) delay(1500);
```

The 1.5 s USB-CDC settle wait is skipped on timer wakes — nobody watches a
serial monitor in a greenhouse. That's 1.5 s of 86.5 mA saved, 96 times a day.

### D. Safety rails that exist specifically because it's battery-powered

```cpp
#define MESH_WAKE_MAX_AWAKE_MS  10000UL   // hard backstop
```

Every wait loop in the wake cycle is bounded by both its own timeout *and* the
global `deadline`. And on radio init failure the node sleeps rather than
restarts:

```cpp
// CRITICAL: sleep, never a restart loop -- a restart loop on a battery
// node with a persistently-failing radio would drain the pack fast.
goToSleep(ch);
```

A reboot loop at 86.5 mA flattens a 1500 mAh cell in ~17 hours. Sleeping
instead means a broken node loses data but survives to be fixed.

---

## 3. The power math

All constants below are real, from `mesh_config.h` and
`edge_node_esp32_c3.ino`:

```
MESH_SLEEP_INTERVAL_MS   = 900000   (15 min = 900 s)
SENSOR_WARMUP_MS         = 2000
MESH_TX_CONFIRM_WAIT_MS  = 500
MESH_WAKE_DISCOVERY_MS   = 5000
MESH_WAKE_MAX_AWAKE_MS   = 10000
MESH_MIN_SLEEP_MS        = 1000

I_active  ~ 86.5 mA
I_sleep   ~ 55 µA (RTC + LDO quiescent) + 7.5 µA (divider) = 62.5 µA
t_active  ~ 2.5 s   (2 s warm-up || radio bring-up, + ~0.3 s read/send/confirm)
```

### Average current

```
I_avg = (I_active × t_active + I_sleep × t_sleep) / T
      = (86.5 mA × 2.5 s + 0.0625 mA × 897.5 s) / 900 s
      = (216.25 + 56.09) / 900
      = 0.303 mA
```

**Duty cycle = 2.5 / 900 = 0.28%.** The node is asleep 99.72% of its life.

### Daily consumption

```
Wakes/day      = 86400 / 900                        = 96
Active charge  = 86.5 mA × 2.5 s × 96 / 3600        = 5.77 mAh/day
Sleep charge   = 0.0625 mA × 23.93 h                = 1.50 mAh/day
                                              TOTAL ≈ 7.3 mAh/day
```

### Runtime on battery alone (zero sun)

```
1500 mAh / 7.3 mAh/day = 205 days
```

At a realistic 80% usable depth-of-discharge: **~165 days ≈ 5.5 months in
complete darkness.**

### Worst case — every wake hits the 10 s backstop

```
I_avg = (86.5 × 10 + 0.0625 × 890) / 900 = 1.02 mA
      → 24.5 mAh/day → 1500 / 24.5 = 61 days
```

Even permanently orphaned and retrying every cycle, the node survives two
months unaided.

### Why zone2 can't be battery-powered

```
Always-on: 100 mA continuous → 2400 mAh/day → 1500 mAh battery = 15 hours
```

**15 hours vs 205 days — a 330× difference.** That single comparison is the
entire justification for the deep-sleep work.

### The single biggest remaining win: the power LED

The Super Mini's power LED draws ~3 mA:

```
3 mA × 24 h = 72 mAh/day
```

**The LED alone is 10× the node's entire optimized budget.** Leave it on and
the 205-day battery becomes 19 days. De-soldering it is not optional polish —
it's the difference between the plan working and not working.

### Second-biggest: the DHT22's warm-up *is* the wake cycle

Swapping to a **BME280** (I²C, reads in milliseconds) cuts the wake to ~0.6 s:

```
I_avg = (86.5 × 0.6 + 0.0625 × 899.4) / 900 = 0.120 mA
      → 2.9 mAh/day → 1500 / 2.9 = 520 days
```

2.5× the battery life for a €2 part — and it adds real barometric pressure,
which `weather.py` already records but has no sensor source for. This is
already ranked #2 in [`HARDWARE_PARTS_LIST.md`](HARDWARE_PARTS_LIST.md) §2a for
exactly this reason.

---

## 4. Solar self-sufficiency

**Demand: 7.3 mAh/day at 3.2 V ≈ 23 mWh/day.** A single AA alkaline holds
about 100× that.

### Supply from a 6 V / 2 W panel

Athens (the project's locale is `Europe/Athens`), worst-case winter ≈ 2
peak-sun-hours/day:

```
Panel energy at STC                        = 2 W × 2 h    = 4 Wh/day
Real derate (cloud, dirt, angle, temp) ~50%               = 2 Wh/day
TP5000 buck conversion ~70%                               = 1.4 Wh/day
Into a 3.2 V cell = 1.4 Wh / 3.2 V                        ≈ 437 mAh/day
```

**Margin: 437 / 7.3 ≈ 60× the requirement, in the worst month of the year.**

### Break-even sunlight

A TP5000 buck-converting 2 W into a 3.2 V cell delivers roughly 0.4 A in good
sun:

```
7.3 mAh / 400 mA = 0.018 h ≈ 66 seconds of direct sun per day
```

Under heavy overcast at ~2% of that current (8 mA): **~55 minutes of dim
daylight per day.** In practice there are several hours of *something* every
day, even in December.

### Why the battery still matters

The panel doesn't have to cover a day — the battery covers **205 days**. The
panel only has to average 7.3 mAh/day across a season. Two solid weeks of dark
storms consumes ~102 mAh, about 7% of the pack, refilled in a couple of minutes
of sun. The system isn't marginal; it's over-provisioned by roughly two orders
of magnitude.

---

## 5. What each new component does

### 🔋 LiFePO4 18650 (3.2 V, ~1500 mAh) — buffer *and* regulator-eliminator

The load-bearing choice, doing two jobs:

1. **Energy buffer** across nights and cloudy stretches.
2. **It removes the voltage regulator entirely.** LiFePO4's discharge plateau
   is 3.4 V → 3.2 V, which sits *inside* the ESP32-C3's 3.0–3.6 V VDD range, so
   the cell wires **straight to the 3V3 pin**. No LDO means no LDO quiescent
   draw (~40–50 µA) — and that is most of the 55 µA sleep floor. Any other
   chemistry adds a regulator back and roughly doubles the floor.

It's also far safer than LiPo at greenhouse temperatures (no thermal runaway)
and handles 2000+ cycles instead of a few hundred.

**Do not substitute a regular Li-ion 18650.** Its 4.2 V full-charge voltage
would exceed the ESP32's 3V3 rail rating, **and** `batteryPctFromMv()`'s lookup
table is tuned specifically to LiFePO4's curve. That's a firmware + hardware
change, not a swap.

### ☀️ TP5000 — LiFePO4-safe solar charger

- **CC/CV charging** terminating at LiFePO4's correct ~3.6 V float. Above
  ~3.65 V/cell the chemistry degrades; this is what prevents that.
- **Buck topology, not linear.** It steps 6 V down to 3.6 V by *converting* the
  excess into extra current rather than burning it as heat. A linear charger
  would waste (6−3.6)/6 = 40% of the panel.
- **Do not use a TP4056** here — that's a Li-ion charger targeting 4.2 V, which
  is a fire risk with this cell and would over-volt the MCU.

### 🔆 6 V / 2 W monocrystalline panel

The **6 V** rating matters more than the wattage. A panel's maximum-power-point
voltage sags under load and further in heat; a nominal 5 V panel can droop
toward 4 V on a hot day, leaving too little headroom above the 3.6 V charge
target plus the charger's dropout. 6 V nominal keeps the charger in regulation
even when hot and partly shaded.

### 📏 The 2 × 220 kΩ divider — the measuring instrument

`battery+ ── 220 kΩ ── GPIO3 ── 220 kΩ ── GND`. Halves the cell voltage into
ADC range. The high resistance keeps drain to ~7.5 µA (12% of the sleep floor)
— the firmware comment accepts this explicitly rather than adding a switching
part.

### How it all fits together

```
   Panel 6V/2W
        |  (0.3-0.4 A in good sun)
        v
   TP5000  -- CC/CV, cuts off at 3.6V --+
        |                               |
        v                               |
   LiFePO4 18650 3.2V/1500mAh ----------+-- 220k --+-- GPIO3 (ADC)
        |  (no regulator!)              |          |
        v                               |         220k
   ESP32-C3 3V3 pin                     |          |
        +-- GPIO5 --> DHT22  (switched) |         GND
        +-- GPIO4 --> Soil   (switched)
```

---

## 6. How battery state reaches the app

Every hop, in real code:

**1. Node measures** — `edge_node_esp32_c3.ino`:

```cpp
uint16_t readBatteryMv() {
  uint32_t sum = 0;
  for (int i = 0; i < 8; i++) { sum += analogReadMilliVolts(BATT_ADC_PIN); delay(2); }
  uint16_t mv = (uint16_t)((sum / 8) * 2);    // x2 undoes the divider
  return (mv < 2000) ? 0 : mv;                // <2V = pin floating = "not measured"
}
```

8-sample average via `analogReadMilliVolts()` (factory ADC calibration, so no
manual attenuation math). `meshSetBatteryMv()` then stuffs it into
`SensorPacket.battery_mv`.

**2. Over the mesh** — `battery_mv` is a `uint16_t` field in the ESP-NOW
packet, relayed hop-by-hop to the bridge.

**3. Bridge converts mV → %** — `bridge_esp32.ino`'s `batteryPctFromMv()`,
piecewise-linear over a measured table because LiFePO4's curve is too flat for
a formula:

```
3400 mV = 100%   3350 = 90%   3320 = 80%   3300 = 70%
3280 =  60%      3260 = 50%   3250 = 40%   3220 = 30%
3200 =  20%      3000 = 10%   2800 =  0%
```

If `battery_mv == 0` the bridge emits **no battery line at all** — a mains node
has no battery reading rather than a fake one.

**4. Pi republishes** — `pi/scripts/serial_bridge.py`:

```python
client.publish(f'greenhouse/nodes/{mac}/battery', f"{float(msg['pct']):.1f}", retain=True)
```

Retained, so the app gets current state the instant it connects.

**5. App parses** — `MqttConnection.isNodeBatteryTopic` →
`NodeStatus.fromMqttBattery` → `batteryPercent`. The `/mesh` topic separately
carries raw `battery_mv` for diagnostics.

**6. App displays** — the Devices list and the mesh-map node cards, sharing one
threshold map (`app/lib/screens/devices/battery_icon.dart`):

```dart
if (percent > 80) return Icons.battery_full;
if (percent > 50) return Icons.battery_5_bar;
if (percent > 20) return Icons.battery_3_bar;
return Icons.battery_alert;
```

rendered beside `'${node.batteryPercent!.toStringAsFixed(0)} %'`.

---

## 7. Known limits

### There is no charging telemetry

The app shows **state of charge**, not charging state. You cannot tell from the
app whether the panel is currently charging, how much current it's delivering,
or whether the panel has failed. The deep-sleep spec puts this explicitly out
of scope: *"Battery charging telemetry / solar MPPT stats. Only battery voltage
is [reported]."*

The cheapest way to add it: wire the TP5000's `CHRG`/`STDBY` status pin to a
spare GPIO, read it during the wake, ship it as one more flag bit in
`SensorPacket`. Well-contained — one packet field, one bridge branch, one MQTT
topic, one app widget. The software-only alternative (inferring charge from the
voltage trend across wakes) is too noisy on LiFePO4's flat curve to trust.

### The percentage is coarse, by physics

100% → 20% spans only 3400 → 3200 mV — 200 mV across 80% of the scale, i.e.
**2.5 mV per 1%**. The divider doubles any ADC error, so ±10 mV of noise is
±8% state-of-charge. The 8-sample averaging in `readBatteryMv()` is fighting
exactly this. Treat the app's number as a coarse gauge ("full / half / getting
low"), not a fuel gauge.

That flatness is also *why* LiFePO4 is the right pick: the node's supply
voltage barely moves across the whole discharge.

### The power figures are unvalidated on hardware

86.5 mA / 55 µA / 2.5 s come from
[`EDGE_NODE_POWER_OPTIMIZATION.md`](EDGE_NODE_POWER_OPTIMIZATION.md), whose own
status note says the hardware mods (LED removal, LDO bypass, divider soldering)
**remain to be done per board**, with bench validation still open as Task 6 of
`docs/superpowers/plans/2026-07-26-mesh-deep-sleep.md`. The firmware is written
and merged; the sleep-current claim is not yet measured.

A USB power meter or a multimeter in series on a modified board would confirm
the 55 µA figure in about five minutes — **and that single measurement is what
the entire solar plan rests on.**
