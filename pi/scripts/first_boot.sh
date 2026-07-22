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

# Generate this unit's unique TLS certs (no-op if they already exist).
bash "$(dirname "$0")/gen_certs.sh"

# Unique 20-char URL-safe password
PASSWORD=$(openssl rand -base64 21 | tr -d '/+=\n' | head -c 20)

# Dedicated bridge account, separate from the app's -- restricted to
# publish-only on sensor topics via /etc/mosquitto/acl (see
# pi/mosquitto/acl, IMPROVEMENTS.md finding A3). Copy this into
# firmware/libraries/GreenhouseSecrets/secrets.h's MQTT_USER/MQTT_PASS
# when flashing bridge_esp32.
BRIDGE_PASSWORD=$(openssl rand -base64 21 | tr -d '/+=\n' | head -c 20)

# TLS fingerprint (empty string if cert not yet deployed)
FINGERPRINT=$(openssl x509 -fingerprint -sha256 -noout -in "$CERT" 2>/dev/null \
  | cut -d= -f2 || echo "")

# 6-digit numeric PIN: proof-of-possession for POST /pair/confirm (read off
# the unit's physical label, see docs/superpowers/specs/
# 2026-07-17-direct-pi-pairing-design.md). Zero-padded so it's always 6 digits.
PAIR_PIN=$(printf '%06d' $(( $(od -An -N4 -tu4 /dev/urandom) % 1000000 )))

# Device ID = last 4-5 hex chars of MAC (tail -c 5 accounts for sysfs newline) (uppercase)
DEVICE_ID=$(cat /sys/class/net/wlan0/address | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]')

# Update Mosquitto passwords for the 'app' and 'bridge' users
mosquitto_passwd -b "$PASSWD_FILE" app "$PASSWORD"
mosquitto_passwd -b "$PASSWD_FILE" bridge "$BRIDGE_PASSWORD"
systemctl restart mosquitto 2>/dev/null || true

# Write device config (readable by pi user, not world)
cat > "$CONFIG" <<EOF
{
  "device_id": "${DEVICE_ID}",
  "username": "app",
  "password": "${PASSWORD}",
  "port": 8883,
  "tls_fingerprint": "${FINGERPRINT}",
  "pair_pin": "${PAIR_PIN}",
  "bridge_username": "bridge",
  "bridge_password": "${BRIDGE_PASSWORD}"
}
EOF
chmod 640 "$CONFIG"
chown root:pi "$CONFIG"

touch "$SENTINEL"
echo "[first-boot] provisioned device ${DEVICE_ID}: unique MQTT password + TLS certs"
