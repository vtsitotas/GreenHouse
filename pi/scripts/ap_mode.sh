#!/bin/bash
# Configures wlan0 as a WiFi AP for first-time setup.
# Called by greenhouse-ap.service only when .wifi_configured does not exist.
set -e

DEVICE_ID=$(cat /sys/class/net/wlan0/address | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]')
SSID="Greenhouse-${DEVICE_ID}"

# Static IP for AP interface
ip addr flush dev wlan0 2>/dev/null || true
ip addr add 192.168.4.1/24 dev wlan0
ip link set wlan0 up

# Write hostapd config
cat > /etc/hostapd/greenhouse.conf <<EOF
interface=wlan0
driver=nl80211
ssid=${SSID}
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
country_code=GR
EOF

# Write dnsmasq config — DHCP + redirect all DNS to portal
cat > /etc/dnsmasq.d/greenhouse-ap.conf <<EOF
interface=wlan0
bind-interfaces
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/#/192.168.4.1
EOF

# Redirect port 80 → 8080 so captive portal triggers automatically
iptables -t nat -F PREROUTING 2>/dev/null || true
iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 8080

# Start AP services
systemctl unmask hostapd 2>/dev/null || true
systemctl start hostapd
systemctl restart dnsmasq

echo "[ap_mode] hotspot ${SSID} started at 192.168.4.1"
