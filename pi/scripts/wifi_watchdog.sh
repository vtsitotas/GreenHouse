#!/bin/bash
# Runs on boot when .wifi_configured exists. Actively tries to connect to
# home WiFi. Falls back to AP mode if still not connected after timeout.

SENTINEL="/etc/greenhouse/.wifi_configured"
[ -f "$SENTINEL" ] || exit 0

# Wait for NetworkManager and wlan0 to fully settle
sleep 20

# ap_up.sh does both of these, but it is SKIPPED on this boot (its unit has
# ConditionPathExists=!sentinel), so on the client path nothing else unblocks
# the radio or sets a regulatory domain. Hardening only: on a unit imaged by
# Pi Imager the domain already arrives via cfg80211.ieee80211_regdom= on the
# kernel cmdline, so this is normally a no-op. It matters on a unit imaged
# without a country set, where the radio falls back to world domain "00" and
# 2.4 GHz ch12/13 become no-IR -- the Pi can SEE an AP parked there but cannot
# associate, which in the logs is indistinguishable from a wrong password.
rfkill unblock wifi 2>/dev/null || true
iw reg set GR 2>/dev/null || true

# Trigger a WiFi scan so NM can find the SSID before connecting
echo "[wifi-watchdog] scanning for networks..."
nmcli device wifi rescan ifname wlan0 2>&1 || true
sleep 8

# Try to bring up the home connection. Keep the output: it carries the actual
# reason ("The Wi-Fi network could not be found", "Secrets were required", ...)
# and is the single most useful line in the failure report written below.
echo "[wifi-watchdog] attempting to connect greenhouse-home..."
UP_OUT=$(nmcli connection up greenhouse-home 2>&1) || true
echo "$UP_OUT"

# Poll for up to 90 seconds.
#
# Check the wlan0 DEVICE state, NOT `nmcli -f STATE general`. The latter is
# NetworkManager's aggregate NMState, which only reads "connected" at
# CONNECTED_GLOBAL (70) -- and that tier depends on NM's internet connectivity
# check. Raspberry Pi OS Lite installs plain `network-manager` WITHOUT
# network-manager-config-connectivity-debian, so the check is disabled and NM
# synthesises FULL; on such a unit the old aggregate test does work. It stops
# working the moment that package is present (or concheck is configured) and a
# genuinely-associated Pi behind a hotspot with no usable upstream parks at
# "connected (site only)" (CONNECTED_SITE, 60) forever. Polling the device
# state asks the question this script actually means: is wlan0 associated and
# configured? Hardening, not the fix for any observed failure.
for _ in $(seq 1 45); do
    state=$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: '$1=="wlan0"{print $2}')
    if [ "$state" = "connected" ]; then
        echo "[wifi-watchdog] connected OK (wlan0 device state: $state)"
        exit 0
    fi
    sleep 2
done

echo "[wifi-watchdog] no WiFi after timeout — reverting to AP mode"

# Record WHY before reverting, to the boot partition so it is readable by
# pulling the SD into any PC -- the unit is about to drop back to an AP with no
# internet, so the owner may have no other way to see this. Mirrors the
# ap_fail.log that ap_up.sh:59-77 already writes on its own failure path.
# The configured-vs-visible SSID pair is the point: a one-character typo in the
# setup form is otherwise indistinguishable from a wrong password, bad signal,
# or a dead radio, and cost several bench cycles to find (2026-08-10).
B=/boot/firmware
[ -d "$B" ] || B=/boot
{
  echo "=== WIFI JOIN FAILED - reverted to AP mode ==="
  date
  echo "=== configured SSID (byte-exact) ==="
  nmcli -g 802-11-wireless.ssid connection show greenhouse-home 2>&1 | od -c
  echo "=== SSIDs actually visible from this unit ==="
  nmcli -f SSID,CHAN,SIGNAL,SECURITY device wifi list ifname wlan0 2>&1
  echo "=== nmcli activation error ==="
  echo "$UP_OUT"
  echo "=== device status ==="
  nmcli device status 2>&1
  echo "=== regulatory domain ==="
  iw reg get 2>&1 | head -5
  echo "=== watchdog journal (this boot) ==="
  journalctl -u greenhouse-wifi-watchdog -b --no-pager -n 40 2>&1
} > "$B/wifi_fail.log" 2>&1
sync

rm -f "$SENTINEL"
exec /home/pi/greenhouse/scripts/ap_up.sh
