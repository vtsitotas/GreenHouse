# Zero-Touch Customer Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A non-technical customer receives the Pi, plugs it in, connects their phone to its hotspot, enters their home WiFi password once, then opens the Greenhouse app and taps "Find my greenhouse" — fully configured in under 2 minutes with no manual IP entry, no SSH, no QR printing required.

**Architecture:** The Pi boots into AP mode (`Greenhouse-XXXX` hotspot) when no home WiFi is configured. A Flask portal server runs always on port 8080: in AP mode it serves a WiFi setup form; after WiFi is configured it serves a `/pair` JSON endpoint at `pi.local:8080/pair`. The Flutter app gets a "Find on network" button that fetches that endpoint and auto-fills the pairing form. Each Pi has a unique password generated at first boot and stored in `/etc/greenhouse/device.json`.

**Tech Stack:** Bash, Python 3 + Flask, hostapd, dnsmasq, iptables, systemd, Flutter + `http` package

## Global Constraints

- Pi OS: Raspberry Pi OS Bookworm (Debian 12), Python 3.11
- MQTT still uses TCP TLS port 8883 — do NOT change Mosquitto config
- Unique password generated with `openssl rand -base64 15 | tr -d '/+=\n' | head -c 20`
- AP SSID format: `Greenhouse-XXXX` where XXXX = last 4 hex chars of wlan0 MAC (uppercase)
- Pi portal always runs on port 8080 (HTTP, cleartext — LAN only)
- Device config file: `/etc/greenhouse/device.json`
- WiFi sentinel file: `/etc/greenhouse/.wifi_configured` (exists = home WiFi set)
- First-boot sentinel file: `/etc/greenhouse/.provisioned` (exists = password already generated)
- Flutter SDK: `C:\Users\billy\flutter\bin`
- Working directory: `C:\Users\billy\Desktop\diplomatikh`
- Commit from repo root, not from `app/`

---

## File Map

**New Pi files:**
- `pi/scripts/first_boot.sh` — generate unique password, write device.json, update Mosquitto passwd
- `pi/scripts/ap_mode.sh` — configure hostapd + dnsmasq + iptables for AP hotspot
- `pi/portal/portal.py` — Flask: WiFi form (AP mode) + `/pair` JSON endpoint (STA mode)
- `pi/portal/templates/wifi.html` — captive portal WiFi entry page
- `pi/portal/templates/rebooting.html` — "connecting..." page shown after form submit
- `pi/systemd/greenhouse-firstboot.service` — oneshot, runs first_boot.sh once
- `pi/systemd/greenhouse-portal.service` — always-on portal server
- `pi/systemd/greenhouse-ap.service` — oneshot AP mode setup, skipped when WiFi is configured
- `pi/scripts/provision.sh` — factory script: installs deps, copies services, enables them

**Modified app files:**
- `app/pubspec.yaml` — add `http: ^1.2.0`
- `app/android/app/src/main/res/xml/network_security_config.xml` — new file, allow cleartext HTTP to `pi.local`
- `app/android/app/src/main/AndroidManifest.xml` — add INTERNET permission + reference security config
- `app/lib/screens/pairing/pairing_screen.dart` — add "Find on network" button + `_discover()` method

---

### Task 1: First-boot unique password generation

**Files:**
- Create: `pi/scripts/first_boot.sh`
- Create: `pi/systemd/greenhouse-firstboot.service`

**Interfaces:**
- Produces: `/etc/greenhouse/device.json` with keys `device_id`, `username`, `password`, `port`, `tls_fingerprint`
- Produces: `/etc/greenhouse/.provisioned` sentinel (marks first boot as done)
- Consumes: `/etc/mosquitto/passwd`, `/etc/mosquitto/certs/server.crt` (already exist on Pi)

- [ ] **Step 1: Write `pi/scripts/first_boot.sh`**

```bash
#!/bin/bash
# Runs once on first boot to generate a unique MQTT password for this Pi unit.
set -e

SENTINEL="/etc/greenhouse/.provisioned"
CONFIG_DIR="/etc/greenhouse"
CONFIG="$CONFIG_DIR/device.json"
PASSWD_FILE="/etc/mosquitto/passwd"
CERT="/etc/mosquitto/certs/server.crt"

[ -f "$SENTINEL" ] && exit 0

mkdir -p "$CONFIG_DIR"

# Unique 20-char URL-safe password
PASSWORD=$(openssl rand -base64 15 | tr -d '/+=\n' | head -c 20)

# TLS fingerprint (empty string if cert not yet deployed)
FINGERPRINT=$(openssl x509 -fingerprint -sha256 -noout -in "$CERT" 2>/dev/null \
  | cut -d= -f2 || echo "")

# Device ID = last 4 hex chars of wlan0 MAC (uppercase)
DEVICE_ID=$(cat /sys/class/net/wlan0/address | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]')

# Update Mosquitto password for 'app' user
mosquitto_passwd -b "$PASSWD_FILE" app "$PASSWORD"
systemctl reload mosquitto 2>/dev/null || true

# Write device config (readable by pi user, not world)
cat > "$CONFIG" <<EOF
{
  "device_id": "${DEVICE_ID}",
  "username": "app",
  "password": "${PASSWORD}",
  "port": 8883,
  "tls_fingerprint": "${FINGERPRINT}"
}
EOF
chmod 640 "$CONFIG"
chown root:pi "$CONFIG"

touch "$SENTINEL"
echo "[first-boot] provisioned device ${DEVICE_ID} with unique password"
```

- [ ] **Step 2: Write `pi/systemd/greenhouse-firstboot.service`**

```ini
[Unit]
Description=Greenhouse first-boot provisioning
DefaultDependencies=no
Before=sysinit.target
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/home/pi/greenhouse/scripts/first_boot.sh
RemainAfterExit=yes
StandardOutput=journal

[Install]
WantedBy=sysinit.target
```

- [ ] **Step 3: Test the script locally (syntax check)**

```powershell
# Syntax-check the bash script from Windows
bash -n C:\Users\billy\Desktop\diplomatikh\pi\scripts\first_boot.sh
```
Expected: no output (means no syntax errors).

- [ ] **Step 4: Commit**

```bash
git add pi/scripts/first_boot.sh pi/systemd/greenhouse-firstboot.service
git commit -m "feat: first-boot unique password generation for each Pi unit"
```

---

### Task 2: AP mode + captive portal WiFi setup

**Files:**
- Create: `pi/scripts/ap_mode.sh`
- Create: `pi/portal/portal.py`
- Create: `pi/portal/templates/wifi.html`
- Create: `pi/portal/templates/rebooting.html`
- Create: `pi/systemd/greenhouse-portal.service`
- Create: `pi/systemd/greenhouse-ap.service`

**Interfaces:**
- Consumes: `/etc/greenhouse/device.json` (from Task 1)
- Consumes: `/etc/greenhouse/.wifi_configured` sentinel (controls AP vs STA mode)
- Produces: `portal.py` Flask app running on `0.0.0.0:8080`
- Produces: AP hotspot `Greenhouse-XXXX` at `192.168.4.1` when no WiFi configured
- Produces: `/etc/wpa_supplicant/wpa_supplicant.conf` after customer submits WiFi form

- [ ] **Step 1: Write `pi/scripts/ap_mode.sh`**

```bash
#!/bin/bash
# Configures wlan0 as a WiFi AP for first-time setup.
# Called by greenhouse-ap.service only when .wifi_configured does not exist.
set -e

DEVICE_ID=$(cat /sys/class/net/wlan0/address | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]')
SSID="Greenhouse-${DEVICE_ID}"

# Static IP for AP interface
ip addr flush dev wlan0 2>/dev/null || true
ip addr add 192.168.4.1/24 dev wlan0
ip link set wlan0 up

# Write hostapd config
cat > /etc/hostapd/greenhouse.conf <<EOF
interface=wlan0
driver=nl80211
ssid=${SSID}
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
country_code=GR
EOF

# Write dnsmasq config — DHCP + redirect all DNS to portal
cat > /etc/dnsmasq.d/greenhouse-ap.conf <<EOF
interface=wlan0
bind-interfaces
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/#/192.168.4.1
EOF

# Redirect port 80 → 8080 so captive portal triggers automatically
iptables -t nat -F PREROUTING 2>/dev/null || true
iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 8080

# Start AP services
systemctl unmask hostapd 2>/dev/null || true
systemctl start hostapd
systemctl restart dnsmasq

echo "[ap_mode] hotspot ${SSID} started at 192.168.4.1"
```

- [ ] **Step 2: Write `pi/portal/portal.py`**

```python
#!/usr/bin/env python3
"""
Greenhouse portal server — port 8080.
AP mode:  serves WiFi credentials form at /
STA mode: serves pairing JSON at /pair
"""
import json
import os
import subprocess
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder="templates")

_CONFIG = "/etc/greenhouse/device.json"
_WIFI_SENTINEL = "/etc/greenhouse/.wifi_configured"
_WPA_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"


def _ap_mode() -> bool:
    return not os.path.exists(_WIFI_SENTINEL)


def _load_config() -> dict:
    with open(_CONFIG) as f:
        return json.load(f)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def index(path):
    if _ap_mode():
        return render_template("wifi.html")
    config = _load_config()
    return render_template("rebooting.html", ssid="your network", config=config)


@app.route("/connect", methods=["POST"])
def connect():
    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "").strip()
    if not ssid:
        return render_template("wifi.html", error="Please enter your WiFi name."), 400

    wpa = (
        "country=GR\n"
        "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
        "update_config=1\n\n"
        "network={\n"
        f'    ssid="{ssid}"\n'
        f'    psk="{password}"\n'
        "    key_mgmt=WPA-PSK\n"
        "}\n"
    )
    with open(_WPA_CONF, "w") as f:
        f.write(wpa)

    open(_WIFI_SENTINEL, "w").close()

    # Reboot after 2-second delay so response reaches the browser
    subprocess.Popen(["bash", "-c", "sleep 2 && reboot"])
    return render_template("rebooting.html", ssid=ssid, config=None)


@app.route("/pair")
def pair():
    """Returns pairing JSON consumed by the Greenhouse Flutter app."""
    try:
        c = _load_config()
        return jsonify({
            "host_lan":        "pi.local",
            "host_tailscale":  "",
            "port":            c["port"],
            "tls_fingerprint": c["tls_fingerprint"],
            "username":        c["username"],
            "password":        c["password"],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
```

- [ ] **Step 3: Write `pi/portal/templates/wifi.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Set up your Greenhouse</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, sans-serif; background: #f0f4f0;
           display: flex; justify-content: center; align-items: center;
           min-height: 100vh; padding: 20px; }
    .card { background: white; border-radius: 16px; padding: 32px;
            max-width: 380px; width: 100%; box-shadow: 0 4px 20px rgba(0,0,0,.08); }
    h1 { font-size: 22px; color: #1a3a1a; margin-bottom: 8px; }
    p  { color: #666; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
    label { display: block; font-size: 13px; color: #444; margin-bottom: 4px; font-weight: 500; }
    input { width: 100%; padding: 12px 14px; border: 1.5px solid #ddd;
            border-radius: 10px; font-size: 16px; margin-bottom: 16px;
            outline: none; transition: border-color .2s; }
    input:focus { border-color: #2e7d32; }
    button { width: 100%; padding: 14px; background: #2e7d32; color: white;
             border: none; border-radius: 10px; font-size: 16px;
             font-weight: 600; cursor: pointer; }
    button:active { background: #1b5e20; }
    .error { background: #fff3f3; border: 1px solid #ffcdd2;
             color: #c62828; padding: 10px 14px; border-radius: 8px;
             font-size: 14px; margin-bottom: 16px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>🌿 Set up your Greenhouse</h1>
    <p>Connect to your home WiFi so the greenhouse can send you data from anywhere.</p>
    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}
    <form method="post" action="/connect">
      <label for="ssid">WiFi Name</label>
      <input type="text" id="ssid" name="ssid" placeholder="My Home WiFi"
             autocomplete="off" autocorrect="off" spellcheck="false" required>
      <label for="password">WiFi Password</label>
      <input type="password" id="password" name="password" placeholder="••••••••">
      <button type="submit">Connect</button>
    </form>
  </div>
</body>
</html>
```

- [ ] **Step 4: Write `pi/portal/templates/rebooting.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Connecting…</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, sans-serif; background: #f0f4f0;
           display: flex; justify-content: center; align-items: center;
           min-height: 100vh; padding: 20px; text-align: center; }
    .card { background: white; border-radius: 16px; padding: 40px 32px;
            max-width: 380px; width: 100%; box-shadow: 0 4px 20px rgba(0,0,0,.08); }
    .spinner { width: 48px; height: 48px; border: 4px solid #e8f5e9;
               border-top: 4px solid #2e7d32; border-radius: 50%;
               animation: spin 1s linear infinite; margin: 0 auto 24px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    h1 { font-size: 20px; color: #1a3a1a; margin-bottom: 12px; }
    p  { color: #666; font-size: 14px; line-height: 1.6; }
    .step { margin-top: 20px; background: #f9fbe7; border-radius: 10px;
            padding: 16px; text-align: left; font-size: 13px; color: #555; }
    .step b { color: #2e7d32; }
  </style>
</head>
<body>
  <div class="card">
    <div class="spinner"></div>
    <h1>Connecting to WiFi…</h1>
    <p>Your greenhouse is connecting to <strong>{{ ssid }}</strong> and will restart in about 30 seconds.</p>
    <div class="step">
      <b>Next steps:</b><br>
      1. Reconnect your phone to your home WiFi<br>
      2. Open the <b>Greenhouse</b> app<br>
      3. Tap <b>"Find my greenhouse"</b>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 5: Write `pi/systemd/greenhouse-portal.service`**

```ini
[Unit]
Description=Greenhouse pairing portal
After=network.target greenhouse-firstboot.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/greenhouse/portal
ExecStart=/usr/bin/python3 /home/pi/greenhouse/portal/portal.py
Restart=always
RestartSec=5
StandardOutput=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 6: Write `pi/systemd/greenhouse-ap.service`**

```ini
[Unit]
Description=Greenhouse AP mode (first-time WiFi setup)
After=network.target greenhouse-firstboot.service
ConditionPathExists=!/etc/greenhouse/.wifi_configured

[Service]
Type=oneshot
ExecStart=/home/pi/greenhouse/scripts/ap_mode.sh
RemainAfterExit=yes
StandardOutput=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 7: Syntax-check the scripts**

```powershell
bash -n C:\Users\billy\Desktop\diplomatikh\pi\scripts\ap_mode.sh
python3 -m py_compile C:\Users\billy\Desktop\diplomatikh\pi\portal\portal.py
```
Expected: no output from either command.

- [ ] **Step 8: Commit**

```bash
git add pi/scripts/ap_mode.sh pi/portal/ pi/systemd/greenhouse-portal.service pi/systemd/greenhouse-ap.service
git commit -m "feat: AP mode captive portal for zero-touch WiFi provisioning"
```

---

### Task 3: App auto-discovery button

**Files:**
- Modify: `app/pubspec.yaml`
- Create: `app/android/app/src/main/res/xml/network_security_config.xml`
- Modify: `app/android/app/src/main/AndroidManifest.xml`
- Modify: `app/lib/screens/pairing/pairing_screen.dart`

**Interfaces:**
- Consumes: `GET http://pi.local:8080/pair` → JSON `{host_lan, host_tailscale, port, tls_fingerprint, username, password}`
- Consumes: `mqttConnectionProvider` (already public from previous task)

- [ ] **Step 1: Add `http` dependency to `app/pubspec.yaml`**

In `app/pubspec.yaml`, add one line under `dependencies:` after `go_router`:

```yaml
  http: ^1.2.0
```

So the dependencies block becomes:
```yaml
dependencies:
  flutter:
    sdk: flutter
  flutter_riverpod: ^2.5.1
  mqtt_client: ^10.4.0
  flutter_secure_storage: ^9.2.2
  shared_preferences: ^2.3.3
  mobile_scanner: ^5.2.3
  go_router: ^14.6.2
  http: ^1.2.0
  cupertino_icons: ^1.0.8
```

Run from `app/` directory:
```powershell
$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"
cd C:\Users\billy\Desktop\diplomatikh\app
flutter pub get
```
Expected last line: `Resolving dependencies... (should include http 1.2.x)`

- [ ] **Step 2: Create `app/android/app/src/main/res/xml/network_security_config.xml`**

Create the directory and file. Android 9+ blocks cleartext HTTP by default — this whitelists `pi.local` only.

```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="false">pi.local</domain>
    </domain-config>
    <base-config cleartextTrafficPermitted="false"/>
</network-security-config>
```

- [ ] **Step 3: Update `app/android/app/src/main/AndroidManifest.xml`**

Add `android:networkSecurityConfig` to the `<application>` tag and add INTERNET permission. The full file should be:

```xml
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET"/>
    <application
        android:label="greenhouse_app"
        android:name="${applicationName}"
        android:icon="@mipmap/ic_launcher"
        android:networkSecurityConfig="@xml/network_security_config">
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:launchMode="singleTop"
            android:taskAffinity=""
            android:theme="@style/LaunchTheme"
            android:configChanges="orientation|keyboardHidden|keyboard|screenSize|smallestScreenSize|locale|layoutDirection|fontScale|screenLayout|density|uiMode"
            android:hardwareAccelerated="true"
            android:windowSoftInputMode="adjustResize">
            <meta-data
              android:name="io.flutter.embedding.android.NormalTheme"
              android:resource="@style/NormalTheme"
              />
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>
        <meta-data
            android:name="flutterEmbedding"
            android:value="2" />
    </application>
    <queries>
        <intent>
            <action android:name="android.intent.action.PROCESS_TEXT"/>
            <data android:mimeType="text/plain"/>
        </intent>
    </queries>
</manifest>
```

- [ ] **Step 4: Add `_discover()` method and "Find my greenhouse" button to `app/lib/screens/pairing/pairing_screen.dart`**

Add this import at the top of the file (after existing imports):
```dart
import 'package:http/http.dart' as http;
```

Add this method inside `_PairingScreenState` (after `_applyQr`):
```dart
Future<void> _discover() async {
  setState(() { _busy = true; _error = null; });
  try {
    final uri = Uri.parse('http://pi.local:8080/pair');
    final response = await http.get(uri).timeout(const Duration(seconds: 10));
    if (response.statusCode == 200) {
      final j = jsonDecode(response.body) as Map<String, dynamic>;
      _host.text   = j['host_lan']        ?? '';
      _tsHost.text = j['host_tailscale']  ?? '';
      _port.text   = (j['port'] ?? 8883).toString();
      _fp.text     = j['tls_fingerprint'] ?? '';
      _user.text   = j['username']        ?? 'app';
      _pass.text   = j['password']        ?? '';
      setState(() { _busy = false; });
    } else {
      setState(() {
        _error = 'Greenhouse not found. Make sure you are on the same WiFi.';
        _busy = false;
      });
    }
  } catch (_) {
    setState(() {
      _error = 'Greenhouse not found. Make sure you are on the same WiFi.';
      _busy = false;
    });
  }
}
```

In the `build` method, replace the existing `FilledButton.icon` (Scan QR button) section with these three buttons at the top of the Column:

```dart
FilledButton.icon(
  onPressed: _busy ? null : _discover,
  icon: const Icon(Icons.search),
  label: const Text('Find my greenhouse'),
  style: FilledButton.styleFrom(
    minimumSize: const Size.fromHeight(52),
  ),
),
const SizedBox(height: 12),
OutlinedButton.icon(
  onPressed: () async {
    final result = await context.push<String>('/pair/qr');
    if (result != null) _applyQr(result);
  },
  icon: const Icon(Icons.qr_code_scanner),
  label: const Text('Scan QR code'),
),
```

- [ ] **Step 5: Verify with flutter analyze**

```powershell
$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"
cd C:\Users\billy\Desktop\diplomatikh\app
flutter analyze
```
Expected: `No issues found!` (or only the pre-existing 14 info-level style hints).

- [ ] **Step 6: Build and install**

```powershell
$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"
cd C:\Users\billy\Desktop\diplomatikh\app
flutter build apk --debug 2>&1 | Select-Object -Last 3
& "C:\Users\billy\AppData\Local\Android\Sdk\platform-tools\adb.exe" install -r "build\app\outputs\flutter-apk\app-debug.apk"
```
Expected: `Success`

- [ ] **Step 7: Commit**

```bash
git add app/pubspec.yaml app/pubspec.lock app/android/app/src/main/res/xml/network_security_config.xml app/android/app/src/main/AndroidManifest.xml app/lib/screens/pairing/pairing_screen.dart
git commit -m "feat: auto-discover greenhouse on local network via pi.local:8080/pair"
```

---

### Task 4: Factory provision script

This script is run **once by the developer/installer** on a fresh Pi before it ships to the customer. It installs all system dependencies, deploys all files, and enables all services.

**Files:**
- Create: `pi/scripts/provision.sh`

- [ ] **Step 1: Write `pi/scripts/provision.sh`**

```bash
#!/bin/bash
# Factory provisioning script — run once as root on a fresh Pi.
# Usage: sudo bash provision.sh
# Installs all greenhouse services and enables them for auto-start.
set -e

REPO=/home/pi/greenhouse

echo "==> Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
  hostapd dnsmasq iptables \
  python3-flask python3-qrcode \
  mosquitto mosquitto-clients \
  openssl avahi-daemon

echo "==> Stopping default hostapd (we control it manually)..."
systemctl stop hostapd 2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true

echo "==> Making scripts executable..."
chmod +x "$REPO/scripts/first_boot.sh"
chmod +x "$REPO/scripts/ap_mode.sh"

echo "==> Installing systemd services..."
cp "$REPO/systemd/greenhouse-firstboot.service" /etc/systemd/system/
cp "$REPO/systemd/greenhouse-portal.service"    /etc/systemd/system/
cp "$REPO/systemd/greenhouse-ap.service"        /etc/systemd/system/

echo "==> Enabling services..."
systemctl daemon-reload
systemctl enable greenhouse-firstboot
systemctl enable greenhouse-portal
systemctl enable greenhouse-ap

echo ""
echo "==> Done. Reboot to complete provisioning."
echo "    On reboot the Pi will:"
echo "    1. Generate a unique password (first_boot.sh)"
echo "    2. Start as a WiFi hotspot 'Greenhouse-XXXX'"
echo "    3. Serve the pairing portal at 192.168.4.1:8080"
```

- [ ] **Step 2: Syntax check**

```powershell
bash -n C:\Users\billy\Desktop\diplomatikh\pi\scripts\provision.sh
```
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add pi/scripts/provision.sh
git commit -m "feat: factory provision script — installs all services on a fresh Pi"
```

---

## End-to-end customer flow (verification)

After all 4 tasks are complete, the full flow works as follows. Verify this manually on the real Pi:

1. **Factory step** (developer): Run `sudo bash provision.sh` on Pi → reboot
2. **Pi boots** → `greenhouse-firstboot.service` generates unique password → `greenhouse-ap.service` starts hotspot `Greenhouse-XXXX`
3. **Customer**: Phone sees `Greenhouse-XXXX` WiFi → connects → browser auto-opens captive portal page
4. **Customer**: Enters home WiFi name + password → taps "Connect" → Pi reboots
5. **Customer**: Phone reconnects to home WiFi automatically
6. **Customer**: Opens Greenhouse app → tap "Find my greenhouse" → sees fields auto-fill → tap "Connect" → dashboard opens

Total customer effort: connect to a WiFi hotspot + type WiFi password + tap twice in the app.
