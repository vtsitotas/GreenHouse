#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — prepare a master Pi for SD-card cloning ("make firmware").
# ═══════════════════════════════════════════════════════════════════════════
# Wipes per-unit identity and the dev WiFi so every flashed clone boots fresh:
# it regenerates its own MQTT password + AP SSID and starts in setup (AP) mode.
#
# This DELETES the WiFi connection, so SSH will drop. Run it detached so it
# finishes and powers off cleanly:
#     sudo systemd-run --collect --unit=prep bash /home/pi/greenhouse/scripts/prep_image.sh
# Then pull the SD card and image it.
# ═══════════════════════════════════════════════════════════════════════════
set -e
[ "$(id -u)" -eq 0 ] || { echo "Run as root."; exit 1; }

echo "[prep] stopping services..."
systemctl stop greenhouse-portal mosquitto 2>/dev/null || true

echo "[prep] wiping per-unit identity (regenerated on first customer boot)..."
rm -f /etc/greenhouse/.wifi_configured
rm -f /etc/greenhouse/.provisioned
rm -f /etc/greenhouse/device.json
: > /etc/mosquitto/passwd
# Per-unit TLS — regenerated uniquely by first_boot.sh on the customer's first boot.
rm -f /etc/mosquitto/certs/ca.key /etc/mosquitto/certs/ca.crt /etc/mosquitto/certs/ca.srl \
      /etc/mosquitto/certs/server.key /etc/mosquitto/certs/server.crt /etc/mosquitto/certs/server.csr

echo "[prep] wiping sensor history database..."
rm -f /var/lib/greenhouse/greenhouse.db /var/lib/greenhouse/greenhouse.db-wal /var/lib/greenhouse/greenhouse.db-shm

echo "[prep] removing per-unit OS password record..."
rm -f /boot/firmware/INITIAL_PASSWORD.txt /boot/INITIAL_PASSWORD.txt

echo "[prep] resetting machine-id (unique per clone)..."
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id

echo "[prep] clearing logs and shell history..."
journalctl --rotate 2>/dev/null || true
journalctl --vacuum-time=1s 2>/dev/null || true
rm -rf /var/log/*.log /var/log/journal/* 2>/dev/null || true
rm -f /home/pi/.bash_history /root/.bash_history 2>/dev/null || true

echo "[prep] removing WiFi profiles (dev + AP) — SSH will drop now..."
# Done last: deleting the active WiFi disconnects us. The detached unit keeps
# running and powers off below.
for c in $(nmcli -t -f NAME,TYPE connection show | awk -F: '$2 ~ /wireless/{print $1}'); do
  nmcli connection delete "$c" 2>/dev/null || true
done

sync
echo "[prep] powering off — pull the SD card and image it."
sleep 2
poweroff
