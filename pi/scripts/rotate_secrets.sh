#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — rotate every secret this Pi owns, in one pass.
# ═══════════════════════════════════════════════════════════════════════════
# Why this exists: real credentials were committed to this repo's git history —
# a live WiFi password and MQTT password, plaintext in
# firmware/bridge_esp32/bridge_esp32.ino. Neither moving them out of the tree
# nor the 2026-08-11 filter-repo scrub invalidates them: every clone taken
# before the scrub still has working credentials. Rotation is the only fix, and
# this script does every part of it that lives on the Pi.
#
#     sudo bash /home/pi/greenhouse/scripts/rotate_secrets.sh
#
# NOT handled here (external systems this script has no business touching --
# see SECURITY.md for the full checklist):
#   * home WiFi password (change on the router, then reflash the camera)
#   * HiveMQ Cloud account password (change in the HiveMQ console)
#
# After running: RE-PAIR THE APP. Every credential it holds is now stale.
# ═══════════════════════════════════════════════════════════════════════════
set -e
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }

CONFIG=/etc/greenhouse/device.json
CERTS=/etc/mosquitto/certs
PASSWD_FILE=/etc/mosquitto/passwd
ROTATE_CERTS="${ROTATE_CERTS:-1}"

[ -f "$CONFIG" ] || { echo "No $CONFIG — run install.sh first."; exit 1; }

echo "==> Rotating MQTT passwords (app + bridge)..."
APP_PASSWORD=$(openssl rand -base64 21 | tr -d '/+=\n' | head -c 20)
BRIDGE_PASSWORD=$(openssl rand -base64 21 | tr -d '/+=\n' | head -c 20)
mosquitto_passwd -b "$PASSWD_FILE" app    "$APP_PASSWORD"
mosquitto_passwd -b "$PASSWD_FILE" bridge "$BRIDGE_PASSWORD"

echo "==> Rotating history API token..."
API_TOKEN=$(openssl rand -base64 33 | tr -d '/+=\n' | head -c 32)

echo "==> Rotating pairing PIN..."
PAIR_PIN=$(printf '%06d' $(( $(od -An -N4 -tu4 /dev/urandom) % 1000000 )))

echo "==> Rotating camera token..."
CAM_TOKEN=$(openssl rand -base64 33 | tr -d '/+=\n' | head -c 32)
echo "$CAM_TOKEN" > /etc/greenhouse/cam_token.txt
chown root:pi /etc/greenhouse/cam_token.txt
chmod 640 /etc/greenhouse/cam_token.txt

if [ "$ROTATE_CERTS" = "1" ]; then
  echo "==> Regenerating TLS keypair (invalidates the old pinned fingerprint)..."
  # Deliberate: if the old private key ever leaked, keeping it would leave the
  # MQTT and portal-HTTPS links impersonable no matter what else is rotated.
  # Set ROTATE_CERTS=0 to keep the existing certs (and skip re-pairing purely
  # for the fingerprint).
  rm -f "$CERTS"/ca.key "$CERTS"/ca.crt "$CERTS"/ca.srl \
        "$CERTS"/server.key "$CERTS"/server.crt "$CERTS"/server.csr
  bash "$(dirname "$0")/gen_certs.sh"
fi

CA_FINGERPRINT=$(openssl x509 -fingerprint -sha256 -noout -in "$CERTS/ca.crt" 2>/dev/null | cut -d= -f2 || echo "")
LEAF_FINGERPRINT=$(openssl x509 -fingerprint -sha256 -noout -in "$CERTS/server.crt" 2>/dev/null | cut -d= -f2 || echo "")
FINGERPRINT=$(printf '%s,%s' "$CA_FINGERPRINT" "$LEAF_FINGERPRINT" | sed 's/^,//; s/,$//')

echo "==> Writing new device.json..."
DEVICE_ID=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('device_id',''))")
cat > "$CONFIG" <<EOF
{
  "device_id": "${DEVICE_ID}",
  "username": "app",
  "password": "${APP_PASSWORD}",
  "port": 8883,
  "tls_fingerprint": "${FINGERPRINT}",
  "pair_pin": "${PAIR_PIN}",
  "api_token": "${API_TOKEN}",
  "bridge_username": "bridge",
  "bridge_password": "${BRIDGE_PASSWORD}"
}
EOF
chown root:pi "$CONFIG"
chmod 640 "$CONFIG"

echo "==> Restarting services..."
systemctl restart mosquitto
systemctl restart greenhouse-portal
systemctl restart greenhouse-cam-bridge 2>/dev/null || true

cat <<EOF

════════════════════════════════════════════════════════════════════════
  ROTATION COMPLETE — this Pi's secrets are all new.

  New pairing PIN : ${PAIR_PIN}
  New cam token   : ${CAM_TOKEN}

  NEXT STEPS (none of these are automatic):

  1. RE-PAIR THE APP. Everything it stored is now invalid. Prefer the QR
     path so the new certificate fingerprint is verified out of band.

  2. RE-FLASH THE CAMERA with the new CAM_TOKEN above, in
     firmware/libraries/GreenhouseSecrets/secrets.h. Until then the camera
     cannot authenticate and motion detection stops.

  3. If you use the WiFi-fallback bridge firmware, re-flash it with the
     new bridge MQTT password (see device.json).

  4. STILL TO DO BY HAND (external systems, see SECURITY.md):
       - change the home WiFi password on the router, then reflash the
         camera again with the new one
       - change the HiveMQ Cloud account password in their console, then
         update /etc/greenhouse/hivemq.json

  Verify with: sudo bash $(dirname "$0")/selftest.sh
════════════════════════════════════════════════════════════════════════
EOF
