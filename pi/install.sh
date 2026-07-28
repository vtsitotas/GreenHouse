#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — master installer (Raspberry Pi OS Trixie, NetworkManager)
# ═══════════════════════════════════════════════════════════════════════════
# Run once on a fresh Pi that already has internet (client WiFi via Pi Imager):
#     sudo bash /home/pi/greenhouse/install.sh
#
# Idempotent: safe to re-run. Sets up packages, TLS, Mosquitto, the pairing
# portal, and the first-time-setup access point. After it finishes, test the
# unit, then run scripts/prep_image.sh and clone the SD card.
# ═══════════════════════════════════════════════════════════════════════════
set -e

REPO=/home/pi/greenhouse
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }

echo "==> Installing packages..."
apt-get update -qq
# NB: dnsmasq-base (not dnsmasq) — NetworkManager's 'shared' AP mode uses it for
# DHCP/NAT. The full dnsmasq package would run a conflicting system service.
apt-get install -y -qq \
  mosquitto mosquitto-clients \
  python3-flask \
  python3-paho-mqtt \
  python3-pil \
  python3-pip \
  python3-serial \
  openssl \
  dnsmasq-base \
  iptables \
  rfkill \
  avahi-daemon

echo "==> Installing firebase-admin (for push notifications)..."
# Not available as an apt package. Trixie's Python is "externally managed"
# (PEP 668) — --break-system-packages is required for a direct system-wide
# pip install here, matching this project's existing no-venv convention.
#
# Bench-tested on a real Pi Zero W (2026-07-10) and found two real problems,
# both fixed below:
#   1. /tmp is a ~214MB tmpfs (RAM-backed) on this OS, but firebase-admin's
#      grpcio dependency is a ~190MB wheel on piwheels (armv6l builds are
#      unusually large) — downloading it into /tmp fails with "No space left
#      on device" even though the SD card has plenty of room. TMPDIR redirects
#      pip's temp/download directory to real disk instead.
#   2. pip's resolver can pick the newest grpcio release even when piwheels
#      hasn't built an armv6l wheel for it yet, silently falling back to
#      compiling from source — a multi-hour, memory-hungry build that
#      crashed/rebooted this Pi Zero W (512MB RAM) twice before this was
#      diagnosed. --prefer-binary tells pip to prefer an older version with a
#      prebuilt wheel over a newer version requiring a source build.
mkdir -p /home/pi/pip-tmp
TMPDIR=/home/pi/pip-tmp pip3 install --break-system-packages --resume-retries 5 --prefer-binary firebase-admin

echo "==> Creating directories..."
# /var/log/journal makes journald persistent across reboots (so a failed
# boot-time service can be diagnosed after the fact, e.g. on a shipped unit).
mkdir -p /etc/greenhouse /etc/mosquitto/certs /var/lib/mosquitto /var/log/journal /var/lib/greenhouse
chown pi:pi /var/lib/greenhouse

if [ ! -f /etc/greenhouse/firebase-service-account.json ]; then
  echo "NOTE: /etc/greenhouse/firebase-service-account.json not found."
  echo "      Push notifications will be skipped until you copy your Firebase"
  echo "      service-account key there (see the FCM push notifications spec)."
else
  # greenhouse-weather.service runs as User=pi (not root) — bench-tested
  # 2026-07-10: a root:root key here makes weather.py's Firebase init fail
  # with "Permission denied" on every push, silently (caught and logged, not
  # fatal, but no push ever goes out). Re-chown on every install.sh run in
  # case the key was copied in with different ownership.
  chown pi:pi /etc/greenhouse/firebase-service-account.json
  chmod 600 /etc/greenhouse/firebase-service-account.json
fi

echo "==> Installing captive-portal DNS config..."
# NetworkManager's shared-mode dnsmasq reads this; resolves every domain to the
# Pi so the phone's connectivity probe triggers the captive-portal popup.
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/greenhouse-captive.conf <<EOF
address=/#/192.168.4.1
EOF

echo "==> Making scripts executable..."
chmod +x "$REPO"/scripts/*.sh

echo "==> Installing admin SSH key (survives password rotation + cloning)..."
ADMIN_KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJZTcXERkxSG6Zi/SA8So2tFS+AP3O2b+rfev8S9Ay5B claude-greenhouse'
install -d -m 700 -o pi -g pi /home/pi/.ssh
touch /home/pi/.ssh/authorized_keys
grep -qF "$ADMIN_KEY" /home/pi/.ssh/authorized_keys || echo "$ADMIN_KEY" >> /home/pi/.ssh/authorized_keys
chown pi:pi /home/pi/.ssh/authorized_keys
chmod 600 /home/pi/.ssh/authorized_keys

echo "==> Hardening SSH..."
# Password auth is the standard remote-brute-force surface, and this unit ships
# with a per-unit random OS password that nobody types anyway (key auth is the
# documented path -- see INSTRUCTIONS.md). Disabling it is GUARDED: if there is
# no usable authorized key, leave password auth alone rather than locking the
# owner out of their own Pi. Root login over SSH is disabled unconditionally --
# nothing in this project ever logs in as root.
mkdir -p /etc/ssh/sshd_config.d
if [ -s /home/pi/.ssh/authorized_keys ]; then
  cat > /etc/ssh/sshd_config.d/60-greenhouse.conf <<'EOF'
# Managed by greenhouse install.sh. Key auth only -- an authorized key was
# present when this was written. Delete this file and restart ssh to undo.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
MaxAuthTries 3
EOF
  echo "    Key auth confirmed present -> password auth disabled."
else
  cat > /etc/ssh/sshd_config.d/60-greenhouse.conf <<'EOF'
# Managed by greenhouse install.sh. Password auth deliberately LEFT ENABLED:
# no authorized key was present when this ran, so disabling it would have
# locked this unit out. Add a key to /home/pi/.ssh/authorized_keys and re-run
# install.sh to harden.
PermitRootLogin no
MaxAuthTries 3
EOF
  echo "    WARNING: no SSH key found in /home/pi/.ssh/authorized_keys."
  echo "             Password auth left ENABLED to avoid locking you out."
  echo "             Add a key and re-run install.sh to disable it."
fi
# Validate before restarting: a bad sshd config that fails to start would end
# remote access to this unit entirely.
if sshd -t 2>/dev/null; then
  systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
else
  echo "    WARNING: sshd config validation failed -- reverting SSH hardening."
  rm -f /etc/ssh/sshd_config.d/60-greenhouse.conf
fi

echo "==> Generating TLS certificates (if missing)..."
bash "$REPO/scripts/gen_certs.sh"

echo "==> Configuring Mosquitto..."
cp "$REPO/mosquitto/mosquitto.conf"      /etc/mosquitto/conf.d/greenhouse.conf
cp "$REPO/mosquitto/acl"                 /etc/mosquitto/acl
# NB: no native Mosquitto `connection` bridge here — it never completes a
# handshake against our HiveMQ Cloud cluster (verified: 0 CONNACKs over 9
# days of logs). greenhouse-hivemq-bridge.service replaces it with a small
# paho-mqtt forwarder, which connects fine.
rm -f /etc/mosquitto/conf.d/hivemq-bridge.conf

echo "==> Writing HiveMQ config for portal..."
# Real credentials are never committed (see IMPROVEMENTS.md finding A1) --
# write a placeholder only if nothing is provisioned yet, so a re-run of
# this script doesn't clobber real values written by hand afterward.
if [ ! -f /etc/greenhouse/hivemq.json ]; then
  cp "$REPO/hivemq.json.example" /etc/greenhouse/hivemq.json
  echo "    Placeholder written. Edit /etc/greenhouse/hivemq.json with your"
  echo "    real HiveMQ Cloud credentials for remote/app access to work."
fi

echo "==> Writing ESP32-CAM shared token for cam_bridge..."
# Generated per-unit rather than copied from the tracked example file. The
# example's value is a KNOWN CONSTANT that ships in the repo -- if a unit kept
# it, the camera token would authenticate nothing (anyone reading the repo
# knows it), while still *looking* provisioned. A random default fails safe:
# the camera simply won't authenticate until the value is deliberately synced
# into the flashed secrets.h, which is a visible, diagnosable failure rather
# than silent fleet-wide credential reuse.
if [ ! -f /etc/greenhouse/cam_token.txt ]; then
  openssl rand -base64 33 | tr -d '/+=\n' | head -c 32 > /etc/greenhouse/cam_token.txt
  echo "" >> /etc/greenhouse/cam_token.txt
  echo "    Generated a unique token for this unit. Copy it into CAM_TOKEN in"
  echo "    firmware/libraries/GreenhouseSecrets/secrets.h and reflash the"
  echo "    camera -- the two must match exactly:"
  echo "      $(cat /etc/greenhouse/cam_token.txt)"
elif [ "$(tr -d '[:space:]' < /etc/greenhouse/cam_token.txt)" = "your-cam-shared-token" ]; then
  # An earlier install.sh copied the example verbatim. That value is public,
  # so treat it as unprovisioned and replace it.
  openssl rand -base64 33 | tr -d '/+=\n' | head -c 32 > /etc/greenhouse/cam_token.txt
  echo "" >> /etc/greenhouse/cam_token.txt
  echo "    WARNING: this unit was using the repo's placeholder cam token,"
  echo "    which is public. Replaced with a unique one -- update CAM_TOKEN in"
  echo "    the camera's secrets.h and reflash:"
  echo "      $(cat /etc/greenhouse/cam_token.txt)"
fi
# cam_bridge.py runs as `pi` and must read it; nothing else should.
chown root:pi /etc/greenhouse/cam_token.txt
chmod 640 /etc/greenhouse/cam_token.txt

touch /etc/mosquitto/passwd
chown mosquitto:mosquitto /etc/mosquitto/passwd
chmod 640 /etc/mosquitto/passwd
chown -R mosquitto:mosquitto /var/lib/mosquitto

echo "==> Preparing security audit log..."
# Services run as `pi` and append security events here (failed auth, lockouts).
# Created up front with the right ownership so the first event isn't lost to a
# permission error at exactly the moment something is attacking the unit.
touch /var/log/greenhouse-security.log
chown pi:pi /var/log/greenhouse-security.log
chmod 640 /var/log/greenhouse-security.log
# Keep it from growing without bound on a unit under sustained probing.
cat > /etc/logrotate.d/greenhouse-security <<'EOF'
/var/log/greenhouse-security.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    create 640 pi pi
}
EOF

echo "==> Installing avahi service advertisement..."
mkdir -p /etc/avahi/services
cp "$REPO/avahi/greenhouse-http.service" /etc/avahi/services/

echo "==> Installing systemd services..."
cp "$REPO"/systemd/greenhouse-firstboot.service      /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-portal.service         /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-ap.service             /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-wifi-watchdog.service  /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-weather.service        /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-recorder.service       /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-hivemq-bridge.service  /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-cam-bridge.service     /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-serial-bridge.service  /etc/systemd/system/
# demo-only, disabled by default -- enable manually with
# `sudo systemctl enable --now greenhouse-simulator` only on units with no
# real sensors attached (see pi/tools/simulator.py)
cp "$REPO"/systemd/greenhouse-simulator.service      /etc/systemd/system/

echo "==> Installing portal sudoers rule (nmcli/reboot for the pi user)..."
# greenhouse-portal.service runs as `pi`, not root (IMPROVEMENTS.md A2) --
# this grants just the two commands _save_wifi()/scan()/_reboot_soon() need.
install -m 440 "$REPO/portal/greenhouse-portal.sudoers" /etc/sudoers.d/greenhouse-portal
visudo -c -f /etc/sudoers.d/greenhouse-portal

# Ensure Mosquitto starts AFTER first_boot has generated certs on a fresh unit.
mkdir -p /etc/systemd/system/mosquitto.service.d
cat > /etc/systemd/system/mosquitto.service.d/greenhouse.conf <<EOF
[Unit]
After=greenhouse-firstboot.service
EOF

systemctl daemon-reload
systemctl enable greenhouse-firstboot greenhouse-portal greenhouse-ap greenhouse-wifi-watchdog greenhouse-weather greenhouse-recorder greenhouse-hivemq-bridge greenhouse-cam-bridge greenhouse-serial-bridge >/dev/null 2>&1

# The stock hostapd unit is unused (NetworkManager runs the AP). Keep it out
# of the way so it never races for the radio.
systemctl disable hostapd 2>/dev/null || true
systemctl mask hostapd 2>/dev/null || true

echo "==> Keeping this master as a WiFi client during setup..."
# .wifi_configured suppresses the AP so SSH stays up while you work on the
# master. prep_image.sh removes it so shipped clones boot into AP mode.
touch /etc/greenhouse/.wifi_configured

echo "==> Writing default weather location config (Athens — edit /etc/greenhouse/weather.json)..."
[ -f /etc/greenhouse/weather.json ] || cat > /etc/greenhouse/weather.json << 'EOF'
{
  "latitude": 37.97,
  "longitude": 23.72,
  "timezone": "Europe/Athens"
}
EOF
# greenhouse-weather.service runs as User=pi and rewrites this file when the
# app pushes a new location — must be writable by pi, not just root.
chown pi:pi /etc/greenhouse/weather.json

echo "==> Writing default automation rules config..."
# The zone1/2/3 dry+humid rules are alert-only (no "action" key) — they're
# a starting point using the specific numbers requested for this farm
# (soil < 15% for 2 days, humidity > 70% for 24h), fully editable/deletable
# via the app's rule builder like any other rule.
[ -f /etc/greenhouse/rules.json ] || cat > /etc/greenhouse/rules.json << 'EOF'
[
  {"id":"rain-close","name":"Close fan on rain","enabled":true,
   "trigger":{"metric":"rain_mm_1h","op":">","value":0.3},
   "action":{"actuator":"fan1","command":"OFF"}},
  {"id":"frost-heat","name":"Frost protection","enabled":true,
   "trigger":{"metric":"temperature","op":"<","value":3},
   "action":{"actuator":"pump1","command":"ON"}},
  {"id":"heat-fan","name":"Heat wave ventilation","enabled":true,
   "trigger":{"metric":"temperature","op":">","value":35},
   "action":{"actuator":"fan1","command":"ON"}},
  {"id":"zone1-dry","name":"Zone 1 soil dry","enabled":true,"notify":true,
   "trigger":{"metric":"zone1/soil_moisture","op":"<","value":15,"duration_minutes":2880}},
  {"id":"zone2-dry","name":"Zone 2 soil dry","enabled":true,"notify":true,
   "trigger":{"metric":"zone2/soil_moisture","op":"<","value":15,"duration_minutes":2880}},
  {"id":"zone3-dry","name":"Zone 3 soil dry","enabled":true,"notify":true,
   "trigger":{"metric":"zone3/soil_moisture","op":"<","value":15,"duration_minutes":2880}},
  {"id":"zone1-humid","name":"Zone 1 too humid","enabled":true,"notify":true,
   "trigger":{"metric":"zone1/air_humidity","op":">","value":70,"duration_minutes":1440}},
  {"id":"zone2-humid","name":"Zone 2 too humid","enabled":true,"notify":true,
   "trigger":{"metric":"zone2/air_humidity","op":">","value":70,"duration_minutes":1440}},
  {"id":"zone3-humid","name":"Zone 3 too humid","enabled":true,"notify":true,
   "trigger":{"metric":"zone3/air_humidity","op":">","value":70,"duration_minutes":1440}}
]
EOF

echo "==> Writing default notification settings..."
[ -f /etc/greenhouse/notification_settings.json ] || cat > /etc/greenhouse/notification_settings.json << 'EOF'
{"frost_forecast": true, "daily_summary": true}
EOF

echo "==> Writing default recorder config..."
[ -f /etc/greenhouse/recorder.json ] || cat > /etc/greenhouse/recorder.json << 'EOF'
{
  "db_path": "/var/lib/greenhouse/greenhouse.db",
  "flush_seconds": 60,
  "raw_days": 90,
  "hourly_days": 730
}
EOF

echo "==> Generating this unit's MQTT credentials..."
bash "$REPO/scripts/first_boot.sh"

# Backfill secrets added to device.json after a unit was already provisioned.
# first_boot.sh exits early on any unit with /etc/greenhouse/.provisioned, so
# without this an existing unit would never gain the newer fields -- and
# because the endpoints that use them fail CLOSED (by design), the symptom
# would be a working-looking unit whose history API returns 401 forever.
if [ -f /etc/greenhouse/device.json ]; then
  python3 - <<'PYEOF'
import json, secrets, string, sys
PATH_ = '/etc/greenhouse/device.json'
try:
    with open(PATH_) as f:
        cfg = json.load(f)
except Exception as exc:
    print(f'    WARN: could not read device.json ({exc}) -- skipping backfill')
    sys.exit(0)

added = []
# 32-char URL-safe token, same shape/entropy as the one first_boot.sh writes.
if not cfg.get('api_token'):
    alphabet = string.ascii_letters + string.digits
    cfg['api_token'] = ''.join(secrets.choice(alphabet) for _ in range(32))
    added.append('api_token')

if added:
    with open(PATH_, 'w') as f:
        json.dump(cfg, f, indent=2)
    print(f'    Backfilled into device.json: {", ".join(added)}')
    print('    NOTE: re-pair the app (or paste the new token under the pairing')
    print('          screen\'s Advanced section) so history keeps working.')
else:
    print('    device.json already has every expected secret.')
PYEOF
  # json.dump above rewrites the file, so re-assert ownership/permissions.
  chown root:pi /etc/greenhouse/device.json
  chmod 640 /etc/greenhouse/device.json
fi
# Mosquitto 2.x wants the passwd file owned by root; mosquitto group reads it.
chown root:mosquitto /etc/mosquitto/passwd
chmod 640 /etc/mosquitto/passwd

echo "==> Restarting services..."
systemctl restart mosquitto
systemctl restart greenhouse-portal
systemctl restart greenhouse-weather
systemctl restart greenhouse-recorder
systemctl restart greenhouse-hivemq-bridge
systemctl restart greenhouse-cam-bridge
# greenhouse-serial-bridge is deliberately NOT restarted here -- it's enabled
# for next boot only. Until the one-time raspi-config step below has run and
# the Pi has rebooted, /dev/serial0 is still the login console, so starting
# it now would just restart-loop against a port it can't use yet. (Unlike
# serial-bridge, cam-bridge has no such dependency, so it's restarted above.)

echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "  MANUAL STEP REQUIRED (not automated by this script):"
echo "  greenhouse-serial-bridge talks to the bridge_esp32 over the Pi's"
echo "  primary UART (/dev/serial0), which Raspberry Pi OS wires to the login"
echo "  console by default. Free it up:"
echo "    sudo raspi-config"
echo "      Interface Options -> Serial Port"
echo "        'login shell over serial'      -> No"
echo "        'serial port hardware enabled' -> Yes"
echo "    then reboot."
echo "  This is a manual step on purpose: it edits boot config, and getting it"
echo "  wrong can lock out serial-console access on a unit someone already"
echo "  depends on -- not something this script should risk doing unattended."
echo "  See INSTRUCTIONS.md for the wiring diagram and full steps."
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "==> Done. Verify with:  sudo bash $REPO/scripts/selftest.sh"
echo "    When happy, prep for cloning:  sudo bash $REPO/scripts/prep_image.sh"
