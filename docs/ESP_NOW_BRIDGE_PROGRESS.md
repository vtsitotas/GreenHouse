# ESP-NOW to MQTT Translator Bridge — Progress Report

> **Status:** ✅ Working (Development/Test Phase)
> **Last Updated:** 2026-06-28

---

## Objective

Build a temporary wireless development bridge to test data flow from a remote sensor node to the Raspberry Pi MQTT broker, before moving to a permanent hardwired cable solution.

## Hardware Stack

| Role | Device | Connection |
|---|---|---|
| **Edge Node** (Sensor) | ESP32-C3 Super Mini | Sends data via ESP-NOW (wireless) |
| **Bridge** (Translator) | ESP32-C3 Super Mini | Receives ESP-NOW → Forwards to MQTT over Wi-Fi |
| **Server** (Broker) | Raspberry Pi (`greenhouse`) | Runs Eclipse Mosquitto MQTT broker |

## Hardware Parts List (For Solar-Powered Edge Node)

**Microcontroller & Sensors:**
- 1x ESP32-C3 Super Mini
- 1x DHT22 / AM2302 Sensor (Temperature & Humidity)
- 1x Capacitive Soil Moisture Sensor v2.0

**Power & Solar (LiFePO4 Architecture):**
- 1x 18650 LiFePO4 Battery (Nominal 3.2V, ~1500mAh to 1800mAh)
- 1x 18650 Single Battery Holder
- 1x TP5000 Solar Charging Module (Configured for 3.6V LiFePO4)
- 1x Mini Monocrystalline Solar Panel (5V/1W or 6V/2W, approx 110x60mm)

**Enclosure & Cabling:**
- 1x IP65 or IP67 Waterproof Junction Box
- 1x Cable Gland (PG7 or PG9)
- 22 AWG or 24 AWG Silicon Wire & Dupont Jumper Wires

## Network Configuration

| Parameter | Value |
|---|---|
| Wi-Fi SSID | `billredmi` |
| Raspberry Pi IP | `10.70.155.202` |
| MQTT Port | `1884` (temporary dev port to avoid conflict with production config) |
| MQTT Topic | `test/sensors/data` |
| Bridge MAC Address | `20:6E:F1:6C:A1:B0` |
| Authentication | Anonymous (dev only) |

---

## What We Built

### 1. Bridge ESP32 (Code #1)
- Connects to `billredmi` Wi-Fi network
- Initializes ESP-NOW to receive incoming packets from any Edge Node
- On packet received: extracts sender MAC + sensor payload → formats as JSON → publishes to Mosquitto via `PubSubClient` library
- Auto-reconnects to MQTT if connection drops

### 2. Edge Node ESP32-C3 (Code #2)
- Scans for `billredmi` Wi-Fi to determine the correct Wi-Fi channel
- Forces ESP-NOW to use that exact channel (critical for Bridge compatibility)
- Sends a `struct_message` containing simulated `temperature` and `humidity` floats every 5 seconds
- **Self-healing:** After 3 consecutive send failures, automatically re-scans the Wi-Fi channel and recovers

### 3. Raspberry Pi Mosquitto Config
- Added `/etc/mosquitto/conf.d/local.conf` with a dedicated dev listener on port `1884` with anonymous access
- Production config in `greenhouse.conf` left completely untouched (ports 1883/8883/9001)

---

## Issues Encountered & Resolved

| # | Issue | Root Cause | Fix |
|---|---|---|---|
| 1 | `esp_now_register_recv_cb` compilation error | ESP32 Arduino Core **v3.x** changed callback signatures | Updated `OnDataRecv` to use `const esp_now_recv_info_t *info` instead of `const uint8_t *mac_addr` |
| 2 | `esp_now_register_send_cb` compilation error | Same Core v3.x API change for the send callback | Updated `OnDataSent` to use `const wifi_tx_info_t *info` instead of `const uint8_t *mac_addr` |
| 3 | MQTT connection `rc=-2` | Mosquitto v2.0+ blocks external connections by default | Created `/etc/mosquitto/conf.d/local.conf` to open a dev listener |
| 4 | Mosquitto service crash on restart | Port `1883` conflict with existing `greenhouse.conf` loopback listener | Used port `1884` for the temporary dev listener to avoid collision |
| 5 | Intermittent `Send Status: FAIL` | Edge Node scans Wi-Fi channel only once at boot; if Bridge isn't ready yet, it locks onto wrong channel | Added self-healing logic: after 3 failures, Edge Node re-scans and re-syncs to the correct channel |

---

## How to Test (Boot Order)

1. **Pi:** Ensure Mosquitto is running → `sudo systemctl status mosquitto`
2. **Bridge ESP32:** Plug into one of the Pi's USB ports (just for power). Wait ~5 seconds for Wi-Fi + MQTT connect.
3. **Edge Node ESP32-C3:** Power on. It will scan, find the channel, and start sending.
4. **Monitor on Pi:**
   ```bash
   mosquitto_sub -h localhost -p 1884 -t "test/sensors/data"
   ```

Expected output every 5 seconds:
```json
{"mac":"XX:XX:XX:XX:XX:XX","temperature":25.30,"humidity":55.70}
```

---

## Dependencies

### Arduino (Both ESP32s)
- **Board Package:** `esp32` by Espressif (v3.x) — installed via Board Manager
- **Library:** `PubSubClient` by Nick O'Leary — installed via Library Manager (Bridge only)

### Raspberry Pi
- **Mosquitto:** `sudo apt install mosquitto mosquitto-clients`

---

## Files Reference

| File | Location | Purpose |
|---|---|---|
| Bridge Code | `firmware/bridge_esp32/bridge_esp32.ino` | ESP-NOW receiver → MQTT publisher |
| Edge Node Code | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` | Simulated sensor → ESP-NOW transmitter |
| Dev MQTT Config | `/etc/mosquitto/conf.d/local.conf` | Opens port 1884 for anonymous dev access |
| Production MQTT Config | `/etc/mosquitto/conf.d/greenhouse.conf` | Existing TLS/WebSocket production setup (untouched) |

---

## Next Steps

- [ ] Replace simulated sensor data with real DHT22/BME280 readings on the Edge Node
- [ ] Transition from wireless Wi-Fi bridge to permanent USB hardwired serial connection
- [ ] Remove `/etc/mosquitto/conf.d/local.conf` dev config when no longer needed
