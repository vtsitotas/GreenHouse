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
  iptables \
  rfkill

echo "==> Creating directories..."
# /var/log/journal makes journald persistent across reboots (so a failed
# boot-time service can be diagnosed after the fact, e.g. on a shipped unit).
mkdir -p /etc/greenhouse /etc/mosquitto/certs /var/lib/mosquitto /var/log/journal

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

echo "==> Generating TLS certificates (if missing)..."
bash "$REPO/scripts/gen_certs.sh"

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

# Ensure Mosquitto starts AFTER first_boot has generated certs on a fresh unit.
mkdir -p /etc/systemd/system/mosquitto.service.d
cat > /etc/systemd/system/mosquitto.service.d/greenhouse.conf <<EOF
[Unit]
After=greenhouse-firstboot.service
EOF

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
