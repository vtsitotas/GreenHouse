# Greenhouse IoT -- Session Handoff

**Last updated:** 2026-06-26
**Status:** Factory provisioning built + AP mode debugging in progress

---

## Quick Start (next session)

```powershell
# SSH into dev Pi
$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"
ssh pi@192.168.1.88
# password: greenhouse2026

# Build + install app on phone (USB connected)
cd C:\Users\billy\Desktop\diplomatikh\app
flutter build apk --debug
& "C:\Users\billy\AppData\Local\Android\Sdk\platform-tools\adb.exe" install -r "build\app\outputs\flutter-apk\app-debug.apk"
```

---

## Project Location

```
C:\Users\billy\Desktop\diplomatikh\
```

> The old folder `C:\Users\billy\Desktop\diplomatikih` (Greek name) still exists -- **delete it**, it is a dead copy.

---

## What Was Done This Session (2026-06-26)

### Problem: Dev Pi stuck in AP mode, SSH broken

**Root cause:** `greenhouse-ap.service` has `ConditionPathExists=!/etc/greenhouse/.wifi_configured`.
The `.wifi_configured` flag was never created on the dev Pi (only `.provisioned` was).
So on every boot the AP service fired, killed wlan0, and SSH died.

**Fix applied to dev Pi (192.168.1.88):**
```bash
sudo touch /etc/greenhouse/.wifi_configured
sudo sed -i 's/ systemd\.mask=greenhouse-ap\.service systemd\.mask=greenhouse-firstboot\.service//' /boot/firmware/cmdline.txt
sudo reboot
```

**Rule:** `.wifi_configured` must exist on any Pi that has already been connected to WiFi.
A fresh customer Pi must NOT have this file -- AP mode fires on first customer power-on.

---

### Factory provisioning system (NEW)

Location: `pi/factory/`

| File | Purpose |
|---|---|
| `user-data` | Cloud-init that installs all packages, writes all scripts, generates TLS certs, blinks LED x5, powers off |
| `network-config` | Template with `BENCH_WIFI_SSID` / `BENCH_WIFI_PASSWORD` placeholders |
| `flash.ps1` | Windows helper -- fills in WiFi creds, writes files to SD card |

**How to provision a new Pi:**
1. Flash Pi OS Lite 64-bit with Pi Imager (hostname `greenhouse`, user `pi`, password `greenhouse2026`, SSH on, no WiFi)
2. Keep SD card in PC (mounts as D:)
3. Run:
```powershell
cd C:\Users\billy\Desktop\diplomatikh\pi\factory
.\flash.ps1
# Enter bench WiFi: INALAN_2.4G_YzPd72 + password
```
4. Insert SD into Pi, power on
5. Wait 3-5 minutes -- packages install, TLS certs generate
6. Green LED blinks 5 times -- done
7. Pi powers off automatically
8. Pi is ready to ship -- customer plugs in, Greenhouse-XXXX hotspot appears

**Tested:** First successful auto-provision run completed this session (LED blinked, Pi powered off).

---

### AP mode not starting on new Pi (UNRESOLVED)

After provisioning the new Pi, powering it on for customer flow test showed:
- No `Greenhouse-XXXX` WiFi visible on phone
- Pi not on home network (scan found only 192.168.1.1 and 192.168.1.2)
- Pi shows login prompt on screen -- hostname `greenhouse` correct

**Likely causes to check:**
- `hostapd` failing (regulatory domain / driver issue on this Pi hardware)
- Service not enabled properly in cloud-init runcmd
- Pi Zero W OTG port needed for keyboard debug

**To debug (need one of):**
- USB keyboard + micro-USB OTG adapter plugged into Pi data port
- Pull SD card, read journald logs (needs Linux / WSL with ext4 support)

**Quickest debug command once you have a keyboard:**
```bash
systemctl status greenhouse-ap.service
systemctl status greenhouse-firstboot.service
journalctl -u greenhouse-ap.service --no-pager
```

---

### Next feature: App handles WiFi setup (PLANNED, not built)

**Decision:** Drop the browser captive portal for customer WiFi setup.
The Flutter app will handle it instead.

**Files to create/modify:**

| File | What |
|---|---|
| `pi/portal/portal.py` | Add `/api/connect` JSON endpoint (alongside existing HTML one) |
| `app/lib/services/network_service.dart` | NEW -- reads current WiFi SSID |
| `app/lib/screens/pairing/wifi_setup_screen.dart` | NEW -- WiFi setup form inside the app |
| `app/lib/screens/pairing/pairing_screen.dart` | Detect Greenhouse-XXXX SSID, route to WiFi setup screen |

**New customer flow:**
1. Customer plugs in Pi -- Greenhouse-XXXX hotspot appears
2. Customer connects phone to Greenhouse-XXXX
3. Opens Greenhouse app -- app detects Greenhouse-XXXX SSID
4. App shows WiFi setup screen -- enter home WiFi name + password
5. App POSTs to `http://192.168.4.1:8080/api/connect`
6. Pi saves WiFi creds, creates `.wifi_configured`, reboots
7. App says "Reconnect to home WiFi"
8. Pi joins home network -- app discovers it via "Find my greenhouse"
9. Dashboard shows live data

---

## Pi Details (dev unit)

| Item | Value |
|---|---|
| IP | 192.168.1.88 |
| Hostname | pi.local |
| User | pi |
| Password | greenhouse2026 |
| MQTT port | 8883 (TCP TLS) |
| MQTT user | app |
| MQTT password | greenhouse2026 (this Pi's unique password) |
| Portal | http://pi.local:8080 |

---

## Building the APK

```powershell
$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"
cd C:\Users\billy\Desktop\diplomatikh\app
flutter build apk --debug
```
APK: `app\build\app\outputs\flutter-apk\app-debug.apk`

ADB install:
```powershell
& "C:\Users\billy\AppData\Local\Android\Sdk\platform-tools\adb.exe" install -r "app\build\app\outputs\flutter-apk\app-debug.apk"
```

---

## File Structure

```
diplomatikh/
+-- app/                          <- Flutter app (Android)
|   +-- lib/
|   |   +-- models/               <- SensorReading, NodeStatus, ActuatorState, ConnectionConfig
|   |   +-- connection/           <- MqttConnection (TCP TLS, testConnect)
|   |   +-- repository/           <- GreenhouseRepository (async* streams)
|   |   +-- providers/            <- Riverpod providers
|   |   +-- services/             <- PairingService, [network_service.dart PLANNED]
|   |   +-- theme/                <- AppColors, AppTheme (Material 3, green)
|   |   +-- screens/
|   |       +-- pairing/          <- pairing_screen.dart, [wifi_setup_screen.dart PLANNED]
|   |       +-- dashboard/
|   |       +-- devices/
|   |       +-- control/
|   |       +-- settings/
|   +-- test/                     <- 21 tests
+-- pi/
|   +-- factory/                  <- NEW: flash.ps1, user-data, network-config
|   +-- mosquitto/                <- mosquitto.conf + setup_tls.sh
|   +-- scripts/                  <- first_boot.sh, ap_mode.sh, provision.sh
|   +-- portal/                   <- portal.py (Flask) + templates/
|   +-- systemd/                  <- greenhouse-firstboot/portal/ap .service files
|   +-- tools/                    <- simulator.py, show_qr.py
+-- docs/superpowers/plans/       <- implementation plans
```

---

## Known Issues / TODO

- [ ] `debugPrint()` calls in `mqtt_connection.dart` and `connection_provider.dart` -- remove before thesis demo
- [ ] AP mode not starting on new Pi -- needs keyboard debug or WSL ext4 log read
- [ ] App WiFi setup flow not built yet (see above plan)
- [ ] Tailscale not installed on dev Pi (needed for out-of-home access)
- [ ] iOS not supported (Android only, thesis device is Redmi Note 13 Pro+ 5G)

---

## Next Slices

| Slice | What |
|-------|------|
| NOW | Debug AP mode on new Pi + build app WiFi setup flow |
| 2 | ESP-NOW firmware for C3 sensors + WROOM bridge |
| 3 | InfluxDB time-series + history charts in app |
| 4 | Node-RED automation + Telegram alerts |
| 5 | Cloud relay (multi-customer) |
| 6 | Solar power, IP65 enclosures, field hardening |
