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

# TLS fingerprint (empty string if cert not yet deployed)
FINGERPRINT=$(openssl x509 -fingerprint -sha256 -noout -in "$CERT" 2>/dev/null \
  | cut -d= -f2 || echo "")

# Device ID = last 4-5 hex chars of MAC (tail -c 5 accounts for sysfs newline) (uppercase)
DEVICE_ID=$(cat /sys/class/net/wlan0/address | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]')

# Update Mosquitto password for 'app' user
mosquitto_passwd -b "$PASSWD_FILE" app "$PASSWORD"
systemctl restart mosquitto 2>/dev/null || true

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
echo "[first-boot] provisioned device ${DEVICE_ID}: unique MQTT password + TLS certs"
