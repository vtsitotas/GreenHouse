#!/bin/bash
# Run once on the Pi to generate TLS certs and MQTT credentials.
# Usage: bash setup_tls.sh <tailscale-ip> <app-password> <bridge-password>
# Example: bash setup_tls.sh 100.64.1.5 gh_app_2026 gh_bridge_2026

set -e

TAILSCALE_IP="${1:?Usage: $0 <tailscale-ip> <app-password> <bridge-password>}"
APP_PASS="${2:?}"
BRIDGE_PASS="${3:?}"
CERT_DIR="/home/pi/greenhouse/certs"

mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

echo "==> Generating CA..."
openssl genrsa -out ca.key 2048
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=GreenhouseCA"

echo "==> Generating server certificate..."
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=greenhouse.local"
openssl x509 -req -days 3650 -in server.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt

chmod 600 ca.key server.key

FINGERPRINT=$(openssl x509 -fingerprint -sha256 -noout -in server.crt | cut -d= -f2)
echo ""
echo "==> TLS fingerprint (copy into app pairing): $FINGERPRINT"

echo ""
echo "==> Deploying Mosquitto config..."
sudo cp /home/pi/greenhouse/mosquitto.conf /etc/mosquitto/conf.d/greenhouse.conf

echo "==> Creating MQTT credentials..."
echo "$APP_PASS" | sudo mosquitto_passwd -c -b /etc/mosquitto/passwd app "$APP_PASS"
echo "$BRIDGE_PASS" | sudo mosquitto_passwd -b /etc/mosquitto/passwd bridge "$BRIDGE_PASS"

echo ""
echo "==> Restarting Mosquitto..."
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager | head -5

echo ""
echo "==> Done. Run show_qr.py --tailscale $TAILSCALE_IP --pass $APP_PASS to generate pairing QR."
