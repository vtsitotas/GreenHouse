#!/bin/bash
# Brings up the first-time-setup WiFi access point using NetworkManager.
# Called by greenhouse-ap.service on boot, only when .wifi_configured is absent.
#
# Why NetworkManager instead of hostapd/dnsmasq:
#   On Raspberry Pi OS Trixie, NetworkManager owns wlan0. Fighting it with raw
#   hostapd silently fails (the radio is already claimed). Letting NM run the
#   hotspot ("ipv4.method shared") gives us AP + DHCP + NAT in one managed
#   connection, with no rfkill / DAEMON_CONF pitfalls.
set -e

# SSID is derived from the MAC at runtime, so every cloned SD card broadcasts a
# unique Greenhouse-XXXX without any per-unit configuration.
DEVICE_ID=$(cat /sys/class/net/wlan0/address | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]')
SSID="Greenhouse-${DEVICE_ID}"

# Unblock the radio in case it booted soft-blocked.
rfkill unblock wifi 2>/dev/null || true

# Set regulatory domain explicitly so the chip allows AP beaconing.
iw reg set GR 2>/dev/null || true

# Disconnect wlan0 from any WiFi client connection that netplan may have
# re-established on boot (e.g. from a leftover 50-cloud-init.yaml).
# Without this, nmcli connection up greenhouse-ap silently fails when wlan0
# is already connected as a client.
nmcli device disconnect wlan0 2>/dev/null || true
sleep 2

# Boot-time race fix: this service can start before NetworkManager has finished
# bringing up wlan0, which makes the nmcli calls below fail and no AP appears.
# Wait until the radio is managed/available before touching it.
nm-online -s -t 30 2>/dev/null || true
for _ in $(seq 1 30); do
  st=$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: '$1=="wlan0"{print $2}')
  case "$st" in disconnected|connected|connecting) break ;; esac
  sleep 1
done

# (Re)create the AP profile so the SSID always matches this unit's MAC.
nmcli connection delete greenhouse-ap 2>/dev/null || true
nmcli connection add type wifi ifname wlan0 con-name greenhouse-ap \
  autoconnect no ssid "$SSID"
nmcli connection modify greenhouse-ap \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  802-11-wireless.channel 6 \
  ipv4.method shared \
  ipv4.addresses 192.168.4.1/24

# Retry activation — NetworkManager can still be settling right after boot.
up_ok=0
for _ in $(seq 1 5); do
  if nmcli connection up greenhouse-ap; then up_ok=1; break; fi
  sleep 2
done
if [ "$up_ok" -ne 1 ]; then
  echo "[ap_up] ERROR: could not activate AP after retries" >&2
  # Write diagnostics to boot partition before exiting so user can read it on PC
  B=/boot/firmware
  [ -d "$B" ] || B=/boot
  {
    echo "=== AP FAIL DIAGNOSTICS ==="
    date
    echo "=== iw reg get ==="
    iw reg get 2>&1
    echo "=== nmcli device status ==="
    nmcli device status 2>&1
    echo "=== nmcli connection show ==="
    nmcli connection show 2>&1
    echo "=== rfkill ==="
    rfkill list 2>&1
    echo "=== journalctl -u greenhouse-ap ==="
    journalctl -u greenhouse-ap --no-pager -n 50 2>&1
  } > "$B/ap_fail.log"
  sync
  exit 1
fi

# Remove any stale port-80→8080 redirect left by older installs; the portal
# now binds directly to port 80 so no iptables redirect is needed.
iptables -t nat -D PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 8080 2>/dev/null || true

echo "[ap_up] broadcasting open network '${SSID}' at 192.168.4.1 (captive portal on :80)"
