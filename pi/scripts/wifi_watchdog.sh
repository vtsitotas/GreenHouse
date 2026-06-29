#!/bin/bash
# Runs on boot when .wifi_configured exists. Actively tries to connect to
# home WiFi. Falls back to AP mode if still not connected after timeout.

SENTINEL="/etc/greenhouse/.wifi_configured"
[ -f "$SENTINEL" ] || exit 0

# Wait for NetworkManager and wlan0 to fully settle
sleep 20

# Trigger a WiFi scan so NM can find the SSID before connecting
echo "[wifi-watchdog] scanning for networks..."
nmcli device wifi rescan ifname wlan0 2>&1 || true
sleep 8

# Try to bring up the home connection (log errors to journal)
echo "[wifi-watchdog] attempting to connect greenhouse-home..."
nmcli connection up greenhouse-home 2>&1 || true

# Poll for up to 90 seconds
for _ in $(seq 1 45); do
    state=$(nmcli -t -f STATE general 2>/dev/null | head -1)
    if [ "$state" = "connected" ]; then
        echo "[wifi-watchdog] connected OK"
        exit 0
    fi
    sleep 2
done

echo "[wifi-watchdog] no WiFi after timeout — reverting to AP mode"
rm -f "$SENTINEL"
exec /home/pi/greenhouse/scripts/ap_up.sh
