#!/bin/bash
# Runs on boot when .wifi_configured exists. If home WiFi is not reached
# within 60 seconds, removes the sentinel and falls back to AP mode so the
# user can re-enter credentials without needing a serial console.
set -e

SENTINEL="/etc/greenhouse/.wifi_configured"
[ -f "$SENTINEL" ] || exit 0

for _ in $(seq 1 30); do
    state=$(nmcli -t -f STATE general 2>/dev/null | head -1)
    [ "$state" = "connected" ] && { echo "[wifi-watchdog] connected OK"; exit 0; }
    sleep 2
done

echo "[wifi-watchdog] no WiFi after 60s — reverting to AP mode"
rm -f "$SENTINEL"
exec /home/pi/greenhouse/scripts/ap_up.sh
