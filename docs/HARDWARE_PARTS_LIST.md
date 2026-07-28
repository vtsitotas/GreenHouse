# Hardware Parts List — GreenHouse IoT

A definitive shopping reference for every physical component this project
touches: what's **already integrated** in the firmware/software today, and
two alternatives for each category, ranked by suitability for this project's
actual goals (cheap, compact, battery-optimized where relevant, minimal
firmware risk).

**Price disclaimer:** all €/$ figures are rough AliExpress/hobbyist-market
ballparks at the time of writing, not live quotes — they drift constantly
and vary by region/vendor. Treat them as relative cost class, not invoice
numbers.

**How to read each table:**
- **Rank 1** = what this project already uses, or what best fits it if
  starting fresh — pick this unless you have a specific reason not to.
- **Rank 2** = a solid alternative — usually a straight hardware swap, no
  firmware changes, or a small config change.
- **Rank 3** = viable but with a real tradeoff (cost, complexity, firmware
  rework, or a niche use case) — listed for completeness, not the default.
- **🔧 Firmware impact** column: **None** (drop-in), **Config** (a
  `#define`/constant change, reflash but no logic change), or **Rewrite**
  (real code changes needed — driver, wire format, calibration curve, etc).

---

## 1. Microcontrollers

### 1a. Edge sensor node MCU

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **ESP32-C3 SuperMini** | €2–3 | None (already integrated, `zone1`) | Cheapest, smallest, lowest deep-sleep floor (~55µA with LED/LDO mods per `EDGE_NODE_POWER_OPTIMIZATION.md`) — the entire deep-sleep firmware (`mesh_config.h`, RTC persistence, `Serial1` choices) is tuned around this chip's quirks (2 UARTs only, specific ADC pins, boot-strapping pins to avoid). |
| 2 | **ESP32 WROOM-32 DevKit** | €4–6 | None (already integrated, `zone2`, always-on role) | Dual-core, more GPIO/RAM, but a noticeably higher deep-sleep current floor than the C3 — this project already keeps it as the **non-sleepy** always-on relay node for exactly that reason. Good pick for a node that will always be near mains/solar-buffered power. |
| 3 | **ESP32-S3 Mini/Zero board** | €5–8 | Rewrite | More RAM/PSRAM, native USB, newer silicon — genuinely nicer chip, but every pin mapping, ADC calibration, and the deep-sleep RTC-memory code would need porting/re-verifying. Worth it only if you're adding real on-device processing (e.g. TinyML) to a sensor node. |

### 1b. Bridge MCU

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **ESP32-C3 SuperMini** | €2–3 | None (already integrated) | The current UART-bridge firmware (`bridge_esp32.ino`) is written specifically for this chip's 2-UART limitation (`Serial` = USB debug, `Serial1` = the Pi link on GPIO4/5). Mains-powered here, so its low-power advantage doesn't matter — kept only for parts-bin consistency with the edge nodes (same PMK/LMK peer setup, same `esp_now` behavior). |
| 2 | **ESP32 WROOM-32 DevKit** | €4–6 | Rewrite (small) | Has **three** hardware UARTs, so a genuine `Serial2` is available for the Pi link instead of remapping `Serial1` — arguably a cleaner fit for the bridge role specifically, at the cost of reflashing with adjusted pin `#define`s. Also gives more headroom if the bridge ever needs to do more (e.g. local buffering, a tiny web UI). |
| 3 | **ESP32-S3 DevKit** | €6–10 | Rewrite | Overkill for what the bridge does today, but future-proof if you want the bridge itself to run something heavier (e.g. a local fallback web dashboard when the Pi is down). |

### 1c. Camera module

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **AI-Thinker ESP32-CAM** | €4–6 | None (already integrated) | Matches `cam_esp32.ino`'s pin map exactly (the classic, well-documented AI-Thinker layout). Cheapest way to get OV2640 + microSD in one board. |
| 2 | **ESP32-WROVER-CAM (PSRAM variant)** | €6–9 | Config | Same OV2640 sensor family and largely pin-compatible, but has PSRAM — meaningfully reduces JPEG frame-buffer corruption at higher resolution/quality settings. Easiest upgrade path: same firmware, just enable PSRAM in the camera config struct. |
| 3 | **ESP32-S3-CAM (e.g. Freenove/XIAO Sense)** | €10–15 | Rewrite | Better sensor options, more RAM, native USB — but the whole pin map and camera-init struct needs a rewrite. Worth it if adding on-device motion/object detection instead of the current Pi-side frame-diff approach. |

---

## 2. Sensors

### 2a. Air temperature + humidity

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **DHT22 / AM2302** | €2–4 | None (already integrated) | Cheap, ubiquitous, already wired into `SensorPacket`. Real downside: ~2s read time is the single biggest chunk of a sleepy node's wake-cycle duration. |
| 2 | **BME280** | €2–4 | Rewrite (small: I2C driver + new field) | I2C, reads in milliseconds not seconds — directly cuts sleepy-node awake time, extending battery life further. **Bonus:** real barometric pressure, which `weather.py` already records but has no real sensor source for today. Best pick specifically *because* this project cares about deep-sleep optimization. |
| 3 | **SHT31 / SHT35 (Sensirion)** | €5–9 | Rewrite (small) | Higher accuracy than DHT22, I2C, fast — but pricier for accuracy this project doesn't currently need (greenhouse climate control tolerances are loose). |

### 2b. Soil moisture

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **Capacitive soil moisture sensor v1.2/v2.0 (analog)** | €1–2 | None (already integrated) | Already calibrated in firmware (`SOIL_DRY_VAL`/`SOIL_WET_VAL`). Simple analog read, no library. |
| 2 | **Capacitive soil sensor v2.0, corrosion-resistant coating** | €1.50–2.50 | None | Same analog interface and calibration constants — just a hardware revision with a better-sealed PCB, longer field lifespan in damp soil. Direct swap. |
| 3 | **I2C soil sensor (e.g. Chirp/Catnip)** | €8–15 | Rewrite | Combines soil moisture + soil temperature + light in one I2C part, less analog wiring — but a real firmware rewrite (new driver, new packet fields) and pricier/less common than the analog option. |

### 2c. Light / lux

*(Not yet in firmware — `recorder.py`/`simulator.py` already support a `light/lux` topic, but no real sensor publishes it.)*

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **BH1750** | €1–2 | Rewrite (small: new I2C driver) | Cheap, I2C, wide range (1–65535 lux), simple library, low power — the obvious first real-hardware pick to close this existing gap. |
| 2 | **TSL2591** | €3–5 | Rewrite (small) | Much wider dynamic range (good for very bright direct sun *and* very dim dawn/dusk readings without saturating), still I2C — pick this over BH1750 if you want accurate readings across the full daylight range. |
| 3 | **Analog LDR + voltage divider** | €0.50–1 | Rewrite (small: new ADC read + calibration) | Cheapest option, no library needed — but nonlinear response, needs its own calibration curve (same pattern as the soil sensor's dry/wet constants), and ties up another ADC pin. |

### 2d. Soil temperature

*(Not yet in firmware — a natural companion metric next to soil moisture.)*

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **DS18B20 waterproof probe** | €1.50–2.50 | Rewrite (small: 1-Wire driver) | Purpose-built for exactly this (waterproof sheath, buried-probe form factor), 1-Wire protocol is well-supported on Arduino-ESP32, cheap. |
| 2 | **NTC 10K thermistor + voltage divider** | €0.30–0.80 | Rewrite (small: Steinhart-Hart math) | Cheapest possible option — but needs its own calibration math and isn't waterproof out of the box (needs potting/heat-shrink). |
| 3 | *(Do not bury a DHT22 for this)* | — | — | Not rated for soil/moisture ingress — listed only to rule it out explicitly. |

### 2e. Water level / tank

*(Not yet in firmware — needed if/when an irrigation reservoir is added.)*

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **Float switch (simple on/off)** | €1–2 | Rewrite (small: one GPIO digital read) | Dead simple, one digital pin, perfect for a binary "empty vs not-empty" alert — matches this project's generally minimal-hardware philosophy. |
| 2 | **HC-SR04 ultrasonic distance sensor** | €1.50–3 | Rewrite (small: trigger/echo timing) | Gives a continuous level reading (not just binary) — needs mounting above the tank looking down, and two GPIO pins (trigger + echo) instead of one. |
| 3 | **Submersible hydrostatic pressure sensor (analog/4-20mA)** | €15–30 | Rewrite (needs signal conditioning) | Most accurate, industrial-grade, but real overkill for a greenhouse tank and needs extra analog front-end circuitry (current-loop-to-voltage, or a proper ADC). |

### 2f. Motion detection (camera-side upgrade)

*(Today's motion detection is Pi-side grayscale frame-diffing on every 3s snapshot — always-on power cost on the camera. A PIR could gate the camera itself instead.)*

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **AM312 mini PIR** | €1–2 | Rewrite (small: GPIO interrupt to wake the cam flow) | ~15µA idle, native 3.3V, tiny form factor — best fit if the camera ever gets its own low-power/wake-on-motion mode. |
| 2 | **HC-SR501 PIR** | €1–2 | Rewrite (small) | The classic PIR — adjustable sensitivity/delay via onboard potentiometers, but designed around 5V (needs a 3.3V-tolerant variant or a divider on the output pin). Bulkier than the AM312. |
| 3 | **LD2410 mmWave presence sensor** | €3–5 | Rewrite (UART driver) | Detects stationary presence too (not just motion), immune to false triggers from wind/leaves moving — but UART-based (extra wiring + parsing) and pricier. Worth it only if PIR false-positives from plant movement become a real problem. |

---

## 3. Actuators / motors

**Currently a real gap** (`TODO.md`/`HANDOFF.md`): the app and `weather.py`'s
rule engine already publish to `greenhouse/actuators/<id>/set` — nothing in
this repo subscribes to it and drives real hardware yet. Only
`pi/tools/simulator.py` fakes actuator state today. Every option below needs
**new firmware from scratch** (a small ESP32 actuator-controller sketch, or
GPIO directly off the Pi) — none of this exists yet.

### 3a. Water pump

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **12V DC submersible pump + relay module** | €5–10 (pump) + €1–2 (relay) | Cheapest, simplest on/off control, widely available. Needs a separate 12V supply from the logic-level electronics. |
| 2 | **12V peristaltic dosing pump** | €8–15 | Better for precise small-volume dosing (e.g. liquid nutrients), self-priming — lower flow rate than a submersible pump though. |
| 3 | **Solenoid valve (12V/24V) on existing mains pressure** | €5–12 | If pressurized water is already available on-site, skip the pump entirely and just gate flow with a valve — simplest option *if* that precondition holds. |

### 3b. Ventilation / fan

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **12V DC brushless fan + relay/MOSFET module** | €4–8 | Simple on/off, cheap, low-voltage-safe. |
| 2 | **12V fan + logic-level MOSFET (PWM-capable)** | €5–10 | Same fan, but a MOSFET instead of a relay allows variable speed via PWM — quieter ramp-up/down, better climate control, more firmware complexity (duty-cycle logic). |
| 3 | **AC mains fan + mains-rated relay module** | €10–20+ | For bigger greenhouses needing more airflow than 12V fans provide. Mains voltage is genuinely more dangerous to wire — extra care/insulation required, and a mains-rated (not just any) relay module. |

### 3c. Actuator control/relay module

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **Single-channel opto-isolated relay module (5V/3.3V logic)** | €0.50–1.50 | Cheapest, one module per actuator, opto-isolation protects the MCU from switching transients. |
| 2 | **4/8-channel relay board** | €3–8 | Better fit once you have pump + fan + lights all needing control — one board, still opto-isolated, uses several GPIO pins (or an I2C expander). |
| 3 | **Solid-state relay (SSR)** | €3–6 per channel | No mechanical wear, silent switching — best for a frequently-cycled load (e.g. a PWM-driven fan via a DC SSR), pricier per channel than mechanical relays. |

---

## 4. Power — sensor node battery

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|---|
| 1 | **LiFePO4 18650, 3.2V nominal, ~1500mAh** | €4–6 | None (already assumed) | The bridge's `batteryPctFromMv()` curve is tuned to this exact chemistry (3.40V=100% → 2.80V=0%), and it connects **directly** to the ESP32's 3V3 pin with no regulator — that's the whole reason the ~55µA sleep floor is reachable. Do not substitute a different chemistry without also changing that curve (see rank 3). |
| 2 | **LiFePO4 14500 (AA-sized), ~600mAh** | €3–5 | None | Same voltage curve, same firmware compatibility — smaller/cheaper cell for tighter enclosures, at the cost of shorter runtime between charges. |
| 3 | **Regular Li-ion 18650, 3.7V nominal (higher capacity)** | €2–4 | Rewrite | Cheaper and higher capacity per cell than LiFePO4 — but its 4.2V full-charge voltage would damage the ESP32 if fed directly to 3V3 (needs a buck/LDO regulator), **and** `batteryPctFromMv()`'s curve would need to be rewritten for Li-ion's discharge profile. Real firmware + hardware work, not a drop-in swap. |

## 5. Power — sensor node charger

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **TP5000 solar LiFePO4 charge module** | €1.50–3 | Purpose-built for 3.6V LiFePO4, prevents overcharge — matches the battery pick above exactly. |
| 2 | **CN3065-based (or similar) LiFePO4 solar charger module** | €2–4 | Same LiFePO4-safe function from a different vendor — good fallback if TP5000 stock is unavailable. |
| 3 | **TP4056 (Li-ion charger, NOT LiFePO4-safe)** | €0.50–1 | Do **not** use with the rank-1/2 battery choice — this is the correct pick *only* if you go with rank-3 Li-ion above, and the two choices must be made together. |

## 6. Power — sensor node solar panel

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **6V / 2W monocrystalline panel** | €3–5 | Comfortably covers the ~7mAh/day requirement (`EDGE_NODE_POWER_OPTIMIZATION.md` Scenario B) even on overcast days — the recommended margin. |
| 2 | **5V / 1W monocrystalline panel** | €2–4 | Smaller and cheaper, but a tighter margin — fine in consistently sunny climates, riskier under frequent cloud cover. |
| 3 | **6V / 1W polycrystalline panel** | €1.50–3 | Cheapest option, lower efficiency per cm² — needs a physically larger panel for the same wattage as the monocrystalline options above. |

---

## 7. Power — Pi station battery

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **12V LiFePO4 pack, 6–12Ah (4S + BMS)** | €30–50 | Matches the Pi's ~30Wh/day draw with real margin, safest chemistry for a hot greenhouse enclosure (thermally stable, low fire risk). |
| 2 | **12V SLA (sealed lead-acid)** | €15–30 | Cheaper upfront, heavier, shorter cycle life — a reasonable simpler/BMS-free option for a permanently-installed (non-portable) station. |
| 3 | **12V Li-ion pack (18650-based, 3S4P+ w/ BMS)** | €25–45 | Lighter and higher energy density than either option above — but less thermally tolerant, a real consideration in a greenhouse that can get hot. Listed last specifically for that safety tradeoff. |

## 8. Power — Pi station solar + charge controller

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **20W panel + basic PWM charge controller** | €20–35 total | Matches the Pi's daily draw with margin — the balanced default pick. |
| 2 | **30–50W panel + MPPT charge controller** | €40–70 total | More headroom for future loads (extra cameras, actuator relays) and MPPT extracts meaningfully more power in low-light/cloudy conditions — pricier but future-proof. |
| 3 | **10W panel + PWM controller** | €12–20 total | Minimum viable, thin margin — only if budget-constrained and you've actually measured that real-world runtime fits. |

## 9. Voltage regulation (12V pack → Pi 5V)

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **Buck converter module (LM2596/MP1584-based), 3A+** | €1.50–3 | Cheap, simple, efficient, widely available — the standard pick for this step-down. |
| 2 | **USB-PD trigger + buck combo module** | €3–6 | Same function but terminates in a standard USB-C connector into the Pi's power input — more convenient/standardized cabling. |
| 3 | **Linear regulator (7805-style)** | €0.50–1 | **Not recommended** — wastes the 12V→5V difference as heat at any real current draw, genuinely wrong choice here. Listed only to rule it out. |

---

## 10. Real-time clock (Pi, for off-grid deployments)

*(A Pi with no internet has no time source after a power blip until connectivity returns — matters for recorder/weather timestamps.)*

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **DS3231 module (I2C)** | €1–2 | High accuracy (built-in TCXO), cheap, well supported by `raspi-config`/`hwclock` on Raspberry Pi OS. |
| 2 | **PCF8523 module (I2C)** | €0.80–1.50 | Cheaper, slightly less accurate than the DS3231 — still perfectly fine for this use case's tolerances. |
| 3 | **DS1307 module (I2C)** | €0.60–1.20 | Oldest/cheapest/least accurate of the three — pick only if truly budget-constrained. |

## 11. Cellular (Pi uplink without home WiFi)

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **SIM7600E-H USB/UART 4G module + IoT SIM** | €25–40 (module) | Solid LTE coverage, moderate cost, handles MQTT traffic comfortably — the balanced default. Camera **remote** live view would still be too data-heavy for regular use over this. |
| 2 | **SIM7000-series module (NB-IoT/LTE-M)** | €20–35 | Lower power draw than full LTE — but much lower bandwidth, fine for MQTT-only telemetry, not workable for camera streaming at all. |
| 3 | **Generic 4G USB dongle (Huawei/ZTE-style)** | €15–25 | Cheapest option — but Linux/Pi driver support (PPP/QMI) is sometimes finicky depending on the exact model/firmware. |

## 12. Wireless range extension (ESP-NOW antenna)

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **C3 SuperMini w/ u.FL connector + external 2.4GHz antenna** | +€1–2 over onboard-antenna boards | Cheap upgrade, roughly doubles ESP-NOW range — worth it before reaching for LoRa (see §13). |
| 2 | **Onboard PCB trace antenna (current default)** | €0 (already what's used) | Zero extra cost, shorter range — perfectly fine if nodes sit reasonably close to the bridge or a relay hop. |
| 3 | **High-gain directional 2.4GHz antenna (e.g. Yagi), bridge-side only** | €8–15 | Best for one long point-to-point hop (bridge to a single far relay) — impractical to put on every small sensor node. |

## 13. Long-range option (only if ESP-NOW genuinely falls short on-site)

*(LoRa/LoRaWAN solves sensor↔bridge distance — it does **not** replace the
Pi's own internet uplink, see §11 for that. Try §12's antenna upgrade first;
this is a bigger architectural step.)*

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **Point-to-point LoRa (2× LilyGO/Heltec ESP32+LoRa boards)** | €15–20 each | Cheapest way to genuinely bridge kilometers of distance — but needs the mesh/relay protocol ported onto a new radio layer, real firmware work, not a plug-in. |
| 2 | **Full LoRaWAN gateway (RAK/Dragino) + ChirpStack** | €80–150+ | Standards-based, supports many nodes properly — a much bigger lift (gateway provisioning, ChirpStack server, LoRaWAN device profiles), worth it only for a genuinely large multi-hectare deployment. |
| 3 | **WiFi mesh access points to extend router range** | €20–40 per AP | Doesn't touch the sensor protocol at all (least firmware risk) — but each AP needs mains power, which partly defeats the point of a battery-first deployment. |

---

## 14. Enclosures

| Rank | Part | Cost | Why |
|---|---|---|---|
| 1 | **IP65 ABS junction box** | €2–6 depending on size | Cheap, adequate splash/rain protection — the standard pick for a greenhouse (not fully submerged, but wet/humid). |
| 2 | **IP67 polycarbonate enclosure** | €4–10 | Better UV resistance over time, some options have clear lids — useful if mounting a solar panel behind the enclosure lid itself. |
| 3 | **3D-printed enclosure (PETG)** | material cost only if you already own a printer | Cheapest if tooling is already available, fully customizable — but not truly waterproof without added gasket/silicone sealing work. |

## 15. Raspberry Pi board (central hub)

| Rank | Part | Cost | 🔧 Firmware impact | Why |
|---|---|---|---|
| 1 | **Raspberry Pi Zero W** | €12–18 | None (already integrated) | Cheapest, wireless built-in, and this project's actual service load (Mosquitto, recorder, weather, portal, serial_bridge) already fits comfortably on it per real field notes in `HANDOFF.md`. |
| 2 | **Raspberry Pi Zero 2 W** | €16–20 | None | Same form factor and GPIO pinout — a drop-in upgrade, quad-core, much more headroom for heavier future loads (more cameras, on-device ML). No code changes needed. |
| 3 | **Raspberry Pi 4 Model B (1–2GB)** | €35–55 | None (bigger board) | Much more powerful, but larger, pricier, and more power-hungry — worth it only once you've genuinely outgrown the Zero 2 W or want real local compute (e.g. running inference models). |

---

## Summary — what to buy today vs. what to add later

**Already in the bill of materials (rank 1 across the board), zero firmware changes needed:**
ESP32-C3 SuperMini ×2 (bridge + zone1), ESP32 WROOM-32 (zone2), AI-Thinker
ESP32-CAM, DHT22, capacitive soil sensor, LiFePO4 18650 + TP5000 + 6V/2W
panel (per sleepy node), Raspberry Pi Zero W.

**Closes a real, already-documented gap** (small firmware work, no new
architecture): BH1750 (light — topic already exists, no sensor), DS18B20
(soil temp), DS3231 (Pi RTC for off-grid resilience).

**Requires new firmware from scratch** (biggest lift, but the app/rules
engine already expects it): any actuator (§3) — nothing drives a real
relay/pump/fan today, only the simulator fakes it.

**Only if the site truly demands it** (bigger architectural steps, don't
reach for these first): cellular (§11, only if the Pi has no WiFi at all),
LoRa (§13, only if ESP-NOW's range genuinely isn't enough even with an
external antenna upgrade).
