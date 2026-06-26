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
  openssl \
  dnsmasq-base \
  rfkill

echo "==> Creating directories..."
mkdir -p /etc/greenhouse /etc/mosquitto/certs /var/lib/mosquitto

echo "==> Making scripts executable..."
chmod +x "$REPO"/scripts/*.sh

echo "==> Generating TLS certificates (if missing)..."
CERTS=/etc/mosquitto/certs
if [ ! -f "$CERTS/server.crt" ]; then
  openssl genrsa -out "$CERTS/ca.key" 2048
  openssl req -new -x509 -days 3650 -key "$CERTS/ca.key" -out "$CERTS/ca.crt" \
    -subj "/CN=GreenhouseCA"
  openssl genrsa -out "$CERTS/server.key" 2048
  openssl req -new -key "$CERTS/server.key" -out "$CERTS/server.csr" \
    -subj "/CN=greenhouse.local"
  openssl x509 -req -days 3650 -in "$CERTS/server.csr" \
    -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
    -out "$CERTS/server.crt"
  rm -f "$CERTS/server.csr"
fi
chown -R mosquitto:mosquitto "$CERTS"
chmod 640 "$CERTS"/*.key
chmod 644 "$CERTS"/*.crt

echo "==> Configuring Mosquitto..."
cp "$REPO/mosquitto/mosquitto.conf" /etc/mosquitto/conf.d/greenhouse.conf
touch /etc/mosquitto/passwd
chown mosquitto:mosquitto /etc/mosquitto/passwd
chmod 640 /etc/mosquitto/passwd
chown -R mosquitto:mosquitto /var/lib/mosquitto

echo "==> Installing systemd services..."
cp "$REPO"/systemd/greenhouse-firstboot.service /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-portal.service    /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-ap.service        /etc/systemd/system/
systemctl daemon-reload
systemctl enable greenhouse-firstboot greenhouse-portal greenhouse-ap >/dev/null 2>&1

# The stock hostapd unit is unused (NetworkManager runs the AP). Keep it out
# of the way so it never races for the radio.
systemctl disable hostapd 2>/dev/null || true
systemctl mask hostapd 2>/dev/null || true

echo "==> Keeping this master as a WiFi client during setup..."
# .wifi_configured suppresses the AP so SSH stays up while you work on the
# master. prep_image.sh removes it so shipped clones boot into AP mode.
touch /etc/greenhouse/.wifi_configured

echo "==> Generating this unit's MQTT credentials..."
bash "$REPO/scripts/first_boot.sh"
# Mosquitto 2.x wants the passwd file owned by root; mosquitto group reads it.
chown root:mosquitto /etc/mosquitto/passwd
chmod 640 /etc/mosquitto/passwd

echo "==> Restarting services..."
systemctl restart mosquitto
systemctl restart greenhouse-portal

echo ""
echo "==> Done. Verify with:  sudo bash $REPO/scripts/selftest.sh"
echo "    When happy, prep for cloning:  sudo bash $REPO/scripts/prep_image.sh"
