#!/bin/bash
# Runs once on first boot to generate a unique MQTT password for this Pi unit.
set -e

SENTINEL="/etc/greenhouse/.provisioned"
CONFIG_DIR="/etc/greenhouse"
CONFIG="$CONFIG_DIR/device.json"
PASSWD_FILE="/etc/mosquitto/passwd"
CERT="/etc/mosquitto/certs/server.crt"
# Pin against the CA, not the leaf: Dart/BoringSSL hands onBadCertificate the
# cert that failed verification, which for this self-signed chain is the root
# CA -- never the leaf. Publishing the leaf's fingerprint here made the app's
# pin check compare two different certs, so it could never match and every LAN
# connection failed closed. Pinning the CA is also stable across server-cert
# renewal. See app/lib/connection/mqtt_connection.dart.
CA_CERT="/etc/mosquitto/certs/ca.crt"

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

# Comma-separated list of every cert fingerprint the app should accept, CA
# first then the leaf (empty string if certs not yet deployed). Both are needed
# -- see the CA_CERT note above.
CA_FINGERPRINT=$(openssl x509 -fingerprint -sha256 -noout -in "$CA_CERT" 2>/dev/null \
  | cut -d= -f2 || echo "")
LEAF_FINGERPRINT=$(openssl x509 -fingerprint -sha256 -noout -in "$CERT" 2>/dev/null \
  | cut -d= -f2 || echo "")
FINGERPRINT=$(printf '%s,%s' "$CA_FINGERPRINT" "$LEAF_FINGERPRINT" | sed 's/^,//; s/,$//')

# 6-digit numeric PIN: proof-of-possession for POST /pair/confirm (read off
# the unit's physical label, see docs/superpowers/specs/
# 2026-07-17-direct-pi-pairing-design.md). Zero-padded so it's always 6 digits.
PAIR_PIN=$(printf '%06d' $(( $(od -An -N4 -tu4 /dev/urandom) % 1000000 )))

# Bearer token for the portal's read-only history API (/api/history,
# /api/history/series). Those endpoints used to be reachable by anything on
# the LAN/hotspot with no authentication at all -- a full dump of the unit's
# sensor history to any joined device. Handed to the app in the same PIN-gated
# /pair/confirm response as the MQTT credentials, so it inherits that flow's
# proof-of-possession rather than adding a second thing for the user to enter.
API_TOKEN=$(openssl rand -base64 33 | tr -d '/+=\n' | head -c 32)

# Per-unit camera token. Generated here (not copied from the tracked example)
# so every cloned unit gets its own instead of the whole fleet sharing one
# value that is public in the repo. Must be copied into CAM_TOKEN in the
# camera's flashed secrets.h -- a deliberate manual sync step, surfaced by
# selftest.sh, because the camera is separately flashed hardware.
if [ ! -f "$CONFIG_DIR/cam_token.txt" ]; then
  openssl rand -base64 33 | tr -d '/+=\n' | head -c 32 > "$CONFIG_DIR/cam_token.txt"
  echo "" >> "$CONFIG_DIR/cam_token.txt"
  chown root:pi "$CONFIG_DIR/cam_token.txt"
  chmod 640 "$CONFIG_DIR/cam_token.txt"
fi

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
  "api_token": "${API_TOKEN}",
  "bridge_username": "bridge",
  "bridge_password": "${BRIDGE_PASSWORD}"
}
EOF
chmod 640 "$CONFIG"
chown root:pi "$CONFIG"

touch "$SENTINEL"
echo "[first-boot] provisioned device ${DEVICE_ID}: unique MQTT password + TLS certs"
