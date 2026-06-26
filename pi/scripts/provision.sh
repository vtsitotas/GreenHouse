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

echo "==> Ensuring Mosquitto passwd file exists..."
touch /etc/mosquitto/passwd
chmod 640 /etc/mosquitto/passwd
chown mosquitto:mosquitto /etc/mosquitto/passwd

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
