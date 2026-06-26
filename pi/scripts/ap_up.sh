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

nmcli connection up greenhouse-ap

# Redirect captive-portal probes (HTTP/80) to the Flask portal on 8080 so the
# WiFi-setup page auto-pops when a phone joins. Idempotent.
iptables -t nat -C PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 8080 2>/dev/null \
  || iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 8080

echo "[ap_up] broadcasting open network '${SSID}' at 192.168.4.1 (captive portal on)"
