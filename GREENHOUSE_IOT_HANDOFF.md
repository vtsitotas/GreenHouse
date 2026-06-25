# Greenhouse / Field IoT — Project Handoff

**Last updated:** 2026-06-25
**Owner:** Bill
**Status:** Phase 1 (Foundation) — ~80% complete

---

## 1. What This Project Is

A DIY IoT system for monitoring (and eventually automating) a greenhouse / field. Battery- or mains-powered sensor nodes measure environmental conditions, publish readings over WiFi/MQTT to a central Raspberry Pi server, which stores the data, runs automation rules, and exposes it to a phone app and PC dashboard. Long-term goal includes alerts, predictions, and remote control.

**Design priorities:** local-first (no mandatory cloud), low-power field nodes, secure remote access, one app for Android + iOS.

---

## 2. Hardware Inventory

### Compute
| Device | Role | Notes |
|---|---|---|
| Raspberry Pi Zero W | Central server | Single-core 1GHz, 512MB RAM — **lightweight services only** (Mosquitto fine; full Home Assistant too heavy) |
| ESP32-C3 SuperMini ×8 | Sensor nodes | RISC-V, WiFi + BT5 LE, 4MB flash, USB-C. GPIO: 0-10, 20, 21. Ceramic antenna onboard |
| ESP32-WROOM-32 DevKit | Sensor/actuator node | 38-pin, has 5V/VIN pin + onboard regulator, easier for 5V sensors |

### Sensors
| Sensor | Type | Interface | Address / Pin notes |
|---|---|---|---|
| DHT22 (AM2302) | Temp + humidity | Digital 1-wire | **In use** — single data pin |
| Capacitive Soil Moisture v2.0 | Soil moisture | Analog | Works at 3.3V (recalibrate) |
| BH1750 GY-302 ×10 | Light (lux) | I2C | 0x23 |
| BMP280 ×10 | Pressure + temp | I2C | 0x76 (SDO→GND) |
| AHT25 ×10 | Temp + humidity | I2C | 0x38 (alt to DHT22) |
| HC-SR04 ×10 | Ultrasonic distance | Digital | **Not needed** for greenhouse (maybe tank level) |
| RFID-RC522 ×10 | RFID reader | SPI | **Not needed** for greenhouse |

### Actuators / Power
| Item | Role | Critical note |
|---|---|---|
| SSR G3MB-202P (1-ch) | Switch AC loads | **5V low-level trigger, AC ONLY** — will NOT switch DC pumps. Mains = lethal, enclose it |
| Powerbank | Bench power | Fine for prototyping; some cut off at low current draw (Pi Zero risk) |

---

## 3. Architecture

```
┌─────────────── FIELD / GREENHOUSE ───────────────┐
│  Sensor nodes (ESP32-C3 SuperMini)                │
│     DHT22, soil moisture, BH1750, BMP280          │
│  Actuator nodes (ESP32-WROOM + SSR)               │
│     AC pump / light / fan control                 │
└───────────────────┬───────────────────────────────┘
                    │ MQTT over WiFi (add TLS + auth)
┌───────────────────▼───────────────────────────────┐
│  SERVER — Raspberry Pi (24/7)                     │
│  ├─ Mosquitto    → MQTT broker          [DONE]    │
│  ├─ InfluxDB     → time-series storage  [TODO]    │
│  ├─ Node-RED     → automation rules     [TODO]    │
│  ├─ Grafana      → PC dashboard         [TODO]    │
│  └─ FastAPI      → REST/WebSocket API   [TODO]    │
└───────────────────┬───────────────────────────────┘
                    │ HTTPS + WebSocket (via Tailscale VPN)
┌───────────────────▼───────────────────────────────┐
│  CLIENTS                                          │
│  ├─ Mobile app (Flutter → Android + iOS)  [TODO]  │
│  ├─ PC dashboard (Grafana in browser)     [TODO]  │
│  └─ Push alerts (Telegram bot / ntfy)     [TODO]  │
└───────────────────────────────────────────────────┘
```

---

## 4. Network / Environment

| Thing | Value |
|---|---|
| WiFi SSID | `INALAN_2.4G_YzPd72` (2.4GHz — C3 has no 5GHz) |
| Pi IP (current) | `192.168.1.88` *(DHCP — consider static reservation)* |
| MQTT broker port | 1883 (plaintext for now → move to 8883 + TLS) |
| SSH | `ssh pi@192.168.1.88` |

> **TODO:** Reserve a static IP for the Pi in the router so it doesn't change.

---

## 5. MQTT Topic Structure

Hierarchical, designed for many nodes/zones:

```
greenhouse/zone1/soil/moisture        # %
greenhouse/zone1/air/temperature      # °C
greenhouse/zone1/air/humidity         # %
greenhouse/zone1/light/lux            # lux
greenhouse/weather/pressure           # hPa
greenhouse/actuators/pump1/set        # command IN  ("ON"/"OFF")
greenhouse/actuators/pump1/state      # confirmation OUT
greenhouse/nodes/node1/status         # heartbeat (online/offline via LWT)
greenhouse/nodes/node1/battery        # voltage / %
```

Each node should publish an MQTT **Last Will & Testament** so the broker auto-marks it `offline` if it drops.

---

## 6. Current Prototype Wiring (single node)

**Board in use for testing:** ESP32-C3 SuperMini (soil works) — DHT22 being debugged, may move to WROOM-32.

### ESP32-C3 SuperMini pin map
| C3 Pin | Connected to |
|---|---|
| 3V3 | Both sensors' VCC (via + rail) |
| GND | Both sensors' GND (via − rail) |
| GPIO4 | Soil AOUT ✅ working |
| GPIO5/6/2 | DHT22 DATA ❌ (see Known Issues) |

### ESP32-WROOM-32 pin map (fallback)
| WROOM Pin | Connected to |
|---|---|
| 3V3 | Sensors VCC |
| GND | Sensors GND |
| GPIO4 | DHT22 DATA |
| GPIO34 | Soil AOUT (input-only ADC) |

**Power:** powerbank into USB port. I2C sensors must stay on **3.3V** (5V damages GPIO).

---

## 7. What's Done ✅

- [x] Raspberry Pi flashed (Pi OS Lite 64-bit), WiFi + SSH headless setup
- [x] Found Pi on network via Advanced IP Scanner (`192.168.1.88`)
- [x] Mosquitto MQTT broker installed + running on Pi
- [x] Broker tested with `mosquitto_pub` / `mosquitto_sub`
- [x] Arduino IDE + ESP32 board package installed
- [x] ESP32-C3 confirmed working (Blink + USB CDC enabled)
- [x] **ESP32-C3 → WiFi → MQTT → Pi confirmed working** (test publish received)
- [x] Soil moisture sensor reading correctly on C3 GPIO4
- [x] PubSubClient library installed

---

## 8. Known Issues & Gotchas

| Issue | Cause | Fix |
|---|---|---|
| ESP32-C3 blank Serial Monitor | USB CDC off | `Tools → USB CDC On Boot → Enabled`, re-upload |
| WROOM-32 "Wrong boot mode (0x17)" | Won't auto-enter download mode | Hold **BOOT**, tap **EN**, release BOOT while "Connecting…" |
| **DHT22 reads FAILED** | Wrong pin order / GPIO conflict / lib | Try swapping VCC↔GND; test GPIO4 on WROOM; confirm Adafruit DHT lib 1.4.6 + Unified Sensor. **← currently here** |
| Floating analog pin "detects" sensor | Unconnected ADC reads random | Can't reliably detect in SW; verify by touching sensor (value should change) |
| Pi slow to boot after unplug | Yanked power → filesystem check | Always `sudo shutdown now` first |
| Powerbank cuts Pi power | Low-current auto-shutoff | Use 5V 2A wall charger for permanent server |
| I2C sensors flaky | Wrong voltage | Keep on 3.3V, not 5V |

---

## 9. Software Stack Reference

| Layer | Tool | Status |
|---|---|---|
| Node firmware | Arduino C++ (PubSubClient, Adafruit DHT) | In progress |
| Broker | Mosquitto | ✅ Running |
| Storage | InfluxDB (time-series) | TODO |
| Automation | Node-RED | TODO |
| Charts (PC) | Grafana | TODO |
| API | FastAPI (REST + WebSocket) | TODO |
| Mobile app | Flutter (Android + iOS) | TODO |
| Notifications | Telegram bot or ntfy.sh → later FCM | TODO |
| Remote access | Tailscale VPN | TODO |

---

## 10. Phased Roadmap

| Phase | Deliverable | Status |
|---|---|---|
| 1. Core | Mosquitto + node publishing real sensor data | 🟡 ~80% (DHT22 left) |
| 2. Storage | InfluxDB + Node-RED ingest + Grafana dashboard | ⬜ |
| 3. Automation | Rules (soil < 30% → pump), Telegram alerts | ⬜ |
| 4. API | FastAPI endpoints for readings + control | ⬜ |
| 5. App | Flutter app: live data, control, push | ⬜ |
| 6. Field deploy | 18650 + TP4056 + solar, deep sleep, IP65 boxes | ⬜ |

**Immediate next steps:**
1. Get DHT22 working (or swap to AHT25 — already have 10, I2C, more reliable).
2. Merge soil + temp/humidity into one sketch that publishes to MQTT topics.
3. Confirm readings arrive on Pi via `mosquitto_sub -t "greenhouse/#" -v`.
4. Start Phase 2 (InfluxDB + Grafana).

---

# 11. TODO: The App + Connectivity (Brainstorm & Plan)

Goal: a phone app (Android + iOS) + PC view that connects to the Pi server to show **sensor data, node status, battery life**, and receive **push notifications** — from anywhere, securely.

## 11.1 The connectivity problem

The phone must reach the Pi. Four options, ranked:

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Tailscale VPN** | Secure, no port forwarding, free, works anywhere, encrypted | Tailscale app on phone + Pi | ✅ **Recommended** |
| Cloud MQTT relay (HiveMQ/EMQX) | Works anywhere, simple | Data leaves home, account needed | 🟡 Backup |
| Local network only | Dead simple | Only works on home WiFi | 🟡 Phase-1 testing |
| Port forwarding | "Works" | Exposes Pi to internet — insecure | ❌ Avoid |

**Plan:** Start local-only for development → add Tailscale for remote. Tailscale puts phone + Pi on the same virtual LAN, so the app just hits `http://<pi-tailscale-ip>:8000`.

## 11.2 How data reaches the app

Two clean patterns (can combine):

- **Live data:** Mosquitto already speaks **MQTT over WebSockets** (enable listener on 9001). App subscribes directly → instant push of new readings.
- **History + control:** FastAPI on the Pi exposes REST endpoints backed by InfluxDB:
  ```
  GET  /api/zones                 → list zones/nodes
  GET  /api/zone/{id}/latest      → current readings
  GET  /api/zone/{id}/history?h=24→ last 24h for charts
  POST /api/actuator/{id}         → {"state":"ON"} pump/light control
  GET  /api/nodes/status          → online/offline + battery per node
  ```
  Secured with a JWT token / API key.

## 11.3 Notifications

| Approach | Effort | Best for |
|---|---|---|
| **Telegram bot** | Lowest — no app store, free, instant | Start here. Node-RED → Telegram node → done |
| **ntfy.sh** | Low — subscribe to a topic, self-hostable | Simple phone push without Firebase |
| **Firebase Cloud Messaging (FCM)** | Higher — needed for native Flutter push | The "real" app notifications later |

**Plan:** Phase 3 alerts go out via **Telegram** (fastest win). When the Flutter app is built, add **FCM** for native push. Keep ntfy as a lightweight fallback.

**Alert examples to implement:**
- Soil moisture < 30% for > 1h → "Zone 1 needs water"
- Temp > 35°C → "Greenhouse overheating, vents on"
- Node battery < 20% → "Node 3 battery low"
- Node offline > 15 min (LWT) → "Node 2 went offline"
- Tank/water level low (if added)

## 11.4 Battery life monitoring

Each ESP32 node measures its own battery and publishes it:

- Wire battery + through a **voltage divider** (2× 100kΩ) into an ADC pin (battery can exceed 3.3V).
- Convert ADC → voltage → % (Li-ion: 4.2V=100%, 3.3V≈0%).
- Publish to `greenhouse/nodes/nodeX/battery` each wake cycle.
- App shows a battery icon per node; alert fires under threshold.
- Pair with **deep sleep** so a single 18650 lasts months (wake → read → publish → sleep 5 min).

## 11.5 App feature list (Flutter)

**MVP**
- [ ] Login / pair with server (enter Pi address + token, or QR)
- [ ] Dashboard: live tiles per zone (temp, humidity, soil, light)
- [ ] Node status list: online/offline + battery %
- [ ] Pull-to-refresh + live WebSocket updates
- [ ] Push notification on threshold alerts

**V2**
- [ ] History charts (24h / 7d / 30d) from InfluxDB
- [ ] Manual control: pump/light/fan ON-OFF toggles
- [ ] Editable thresholds & schedules (writes back to Node-RED/server)
- [ ] Multiple greenhouses/sites
- [ ] Dark mode, offline cache of last readings

**Nice-to-have**
- [ ] ESP32-CAM time-lapse plant growth view
- [ ] ML watering prediction ("water likely needed in 2 days")
- [ ] Export data CSV
- [ ] Widget / Apple Watch / Wear OS glance

## 11.6 App build roadmap

| Step | Task |
|---|---|
| 1 | Enable Mosquitto WebSocket listener (9001) on Pi |
| 2 | Stand up FastAPI on Pi with `/latest`, `/history`, `/status`, `/actuator` |
| 3 | Add JWT auth + Tailscale for remote reach |
| 4 | Flutter project skeleton (one codebase → Android + iOS) |
| 5 | Wire app to REST (history) + MQTT-WS (live) |
| 6 | Integrate FCM push; map server alerts → notifications |
| 7 | Build control screen → POST to actuator endpoints |
| 8 | Polish: charts, theming, multi-site, error states |
| 9 | TestFlight (iOS) + internal APK (Android) |

## 11.7 Open decisions

- [ ] **App framework:** Flutter (native push, app-store ready) vs PWA (one web codebase incl. PC, but weak iOS push). → Leaning Flutter for the real app, Grafana covers PC.
- [ ] **Where does FastAPI run?** On the Pi Zero W (may be tight) vs the second Pi vs a small VPS. → Try Pi first, move if sluggish.
- [ ] **Auth model:** single shared token (simple) vs per-user accounts (future-proof).
- [ ] **DC vs AC actuation:** SSR G3MB-202P is AC-only — decide pump type before buying. DC pump → need MOSFET/mechanical relay instead.
- [ ] **Server upgrade?** Pi Zero W is weak for InfluxDB + Grafana + FastAPI together. Consider Pi 4 (2GB) for the always-on stack; keep Zero W as a remote node.

---

*End of handoff. Pick up at Section 10 "Immediate next steps."*
