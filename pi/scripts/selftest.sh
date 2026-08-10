#!/bin/bash
# Verifies a provisioned greenhouse unit without rebooting / dropping SSH.
# Run: sudo bash /home/pi/greenhouse/scripts/selftest.sh
PASS=0; FAIL=0
ok(){ echo "  [ OK ] $1"; PASS=$((PASS+1)); }
no(){ echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "== services enabled =="
for s in greenhouse-firstboot greenhouse-portal greenhouse-ap greenhouse-wifi-watchdog greenhouse-recorder greenhouse-hivemq-bridge greenhouse-serial-bridge greenhouse-weather mosquitto; do
  systemctl is-enabled "$s" >/dev/null 2>&1 && ok "$s enabled" || no "$s not enabled"
done

echo "== services active =="
for s in greenhouse-portal greenhouse-recorder greenhouse-hivemq-bridge greenhouse-serial-bridge greenhouse-weather mosquitto; do
  systemctl is-active "$s" >/dev/null 2>&1 && ok "$s running" || no "$s not running"
done

# The UART link to bridge_esp32 replaced the bridge's old WiFi/MQTT uplink, so
# nothing else in this file would notice it being dead: greenhouse-hivemq-bridge
# is the unrelated local->cloud hop. Without this section a unit whose serial
# link is unplugged, unconfigured, or shadowed by the login console still
# reported a clean pass while receiving no sensor data at all.
echo "== UART bridge (bridge_esp32 link) =="
if [ -e /dev/serial0 ]; then
  ok "/dev/serial0 present -> $(readlink -f /dev/serial0)"
else
  no "/dev/serial0 missing (run raspi-config: disable serial login shell, enable serial hardware)"
fi
if grep -rqs 'console=serial0\|console=ttyAMA0\|console=ttyS0' /boot/firmware/cmdline.txt /boot/cmdline.txt; then
  no "a login console still owns the UART -- serial_bridge.py cannot read it"
else
  ok "UART free of the login console"
fi
# Node liveness is informational: a unit can be legitimately provisioned before
# any ESP32 is wired up, so an absence of nodes must not fail the whole test.
ONLINE=$(timeout 4 mosquitto_sub -h 127.0.0.1 -p 1883 -t 'greenhouse/nodes/+/status' -v 2>/dev/null \
         | awk '$2=="online"' | wc -l)
echo "       nodes currently reporting online: ${ONLINE:-0}"

echo "== mosquitto listeners =="
ss -tlnp 2>/dev/null | grep -q ':8883' && ok "TLS 8883 listening" || no "8883 not listening"

echo "== certs =="
[ -f /etc/mosquitto/certs/server.crt ] && ok "server.crt present" || no "server.crt missing"
sudo -u mosquitto test -r /etc/mosquitto/certs/server.key && ok "mosquitto can read server.key" || no "mosquitto cannot read server.key"

echo "== device config =="
if [ -f /etc/greenhouse/device.json ]; then
  ok "device.json present"
  python3 -c "import json;d=json.load(open('/etc/greenhouse/device.json'));print('       device_id='+d['device_id']+' fp_set='+str(bool(d['tls_fingerprint'])))"
else
  no "device.json missing"
fi

echo "== weather config =="
[ -f /etc/greenhouse/weather.json ] && ok "weather.json present" || no "weather.json missing"
[ -f /etc/greenhouse/rules.json ]   && ok "rules.json present"   || no "rules.json missing"

echo "== mqtt round-trip (TLS, authed) =="
USER=$(python3 -c "import json;print(json.load(open('/etc/greenhouse/device.json'))['username'])" 2>/dev/null)
PASS_=$(python3 -c "import json;print(json.load(open('/etc/greenhouse/device.json'))['password'])" 2>/dev/null)
if timeout 10 mosquitto_pub -h greenhouse.local -p 8883 --cafile /etc/mosquitto/certs/ca.crt \
     -u "$USER" -P "$PASS_" -t greenhouse/selftest -m ok 2>/dev/null; then
  ok "authenticated TLS publish works"
else
  no "TLS publish failed"
fi

echo "== hivemq bridge round-trip =="
python3 - <<'PYEOF'
import json, ssl, time, sys
import paho.mqtt.client as mqtt

cfg = json.load(open('/etc/greenhouse/hivemq.json'))
topic = f'greenhouse/selftest/{int(time.time())}'
seen = {'ok': False}

def on_message(client, userdata, msg):
    if msg.payload == b'ok':
        seen['ok'] = True

remote = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-selftest-remote')
remote.username_pw_set(cfg['username'], cfg['password'])
remote.tls_set(ca_certs='/etc/ssl/certs/ca-certificates.crt', tls_version=ssl.PROTOCOL_TLSv1_2)
remote.on_message = on_message
remote.connect(cfg['host'], cfg['port'], keepalive=10)
remote.subscribe(topic, qos=1)
remote.loop_start()
time.sleep(1)

local = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-selftest-local')
local.connect('127.0.0.1', 1883, keepalive=10)
local.publish(topic, 'ok', qos=1)
local.disconnect()

time.sleep(5)
remote.loop_stop()
remote.disconnect()

if seen['ok']:
    print('       [ OK ] local publish reached HiveMQ Cloud via bridge')
else:
    print('       [FAIL] local publish did not reach HiveMQ within 5s', file=sys.stderr)
    sys.exit(1)
PYEOF
[ $? -eq 0 ] && ok "hivemq bridge forwarding local -> cloud" || no "hivemq bridge not forwarding (check greenhouse-hivemq-bridge.service)"

echo "== portal =="
curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:80/pair | grep -qE '200|403' && ok "portal responding on :80" || no "portal not responding on :80"

echo "== recorder database =="
[ -f /var/lib/greenhouse/greenhouse.db ] && ok "greenhouse.db present" || no "greenhouse.db missing"
python3 - <<'PYEOF'
import sqlite3, time, sys
try:
    conn = sqlite3.connect('file:/var/lib/greenhouse/greenhouse.db?mode=ro', uri=True)
    integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
    assert integrity == 'ok', f'integrity_check returned {integrity}'
    recent = conn.execute(
        'SELECT COUNT(*) FROM readings WHERE ts >= ?',
        (int(time.time()) - 600,)).fetchone()[0]
    assert recent > 0, 'no readings in the last 10 minutes'
    print('       [ OK ] db integrity ok, recent readings present')
except Exception as e:
    print(f'       [FAIL] {e}', file=sys.stderr)
    sys.exit(1)
PYEOF
[ $? -eq 0 ] && ok "recorder db healthy" || no "recorder db unhealthy"
# /api/history* is bearer-token gated. Check BOTH directions: an
# unauthenticated request must be refused, and an authenticated one must work.
# A 200 on the unauthenticated call means the gate has regressed and the
# unit is serving its whole sensor history to anything on the network.
API_TOKEN=$(python3 -c "import json;print(json.load(open('/etc/greenhouse/device.json')).get('api_token',''))" 2>/dev/null)
UNAUTH_CODE=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:80/api/history/series)
[ "$UNAUTH_CODE" = "401" ] && ok "history endpoint rejects unauthenticated requests" \
  || no "history endpoint NOT protected (expected 401, got $UNAUTH_CODE)"
if [ -n "$API_TOKEN" ]; then
  curl -s -m 5 -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $API_TOKEN" \
    http://127.0.0.1:80/api/history/series | grep -q '200' \
    && ok "history endpoint responding (authenticated)" \
    || no "history endpoint not responding to an authenticated request"
else
  no "device.json has no api_token (run install.sh to backfill it)"
fi

echo "== security posture =="
# The camera intake endpoint must refuse an unauthenticated POST -- an open
# one lets any LAN host impersonate the camera and harvest CAM_TOKEN. The
# camera is parked (parked/camera/), so nothing should be listening at all;
# only assert the gate if something actually is, otherwise a parked unit
# reports a security failure for a service it deliberately doesn't run.
if ss -ltn 2>/dev/null | grep -q ':8090'; then
  CAM_CODE=$(curl -s -m 5 -o /dev/null -w "%{http_code}" -X POST \
    --data-binary 'x' -H 'Content-Type: image/jpeg' http://127.0.0.1:8090/cam/frame)
  [ "$CAM_CODE" = "401" ] && ok "cam frame intake rejects unauthenticated POSTs" \
    || no "cam frame intake NOT protected (expected 401, got $CAM_CODE)"
else
  ok "cam frame intake not listening (camera parked)"
fi
# Mosquitto's plaintext listener must never be reachable off-box.
if ss -ltn 2>/dev/null | grep -q '127.0.0.1:1883'; then
  ok "mosquitto plaintext listener bound to loopback only"
elif ss -ltn 2>/dev/null | grep -q ':1883'; then
  no "mosquitto plaintext listener is NOT loopback-only"
else
  no "mosquitto plaintext listener not found"
fi
[ -f /etc/mosquitto/acl ] && grep -q 'nodes/+/battery' /etc/mosquitto/acl \
  && ok "mosquitto ACL covers telemetry topics" \
  || no "mosquitto ACL missing/stale (bridge telemetry would be denied)"
PERM=$(stat -c '%a' /etc/greenhouse/device.json 2>/dev/null)
[ "$PERM" = "640" ] && ok "device.json permissions are 640" \
  || no "device.json permissions are $PERM (expected 640)"
# The portal's HTTPS listener: encrypted transport for pairing + history.
if [ -f /etc/greenhouse/portal.crt ] && [ -f /etc/greenhouse/portal.key ]; then
  ok "portal TLS keypair present"
  curl -sk -m 5 -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $API_TOKEN" \
    https://127.0.0.1:8443/api/history/series | grep -q '200' \
    && ok "portal HTTPS (8443) responding" \
    || no "portal HTTPS (8443) not responding"
else
  no "portal TLS keypair missing (run gen_certs.sh / install.sh)"
fi
if [ -f /etc/greenhouse/require_https ]; then
  ok "HTTPS enforcement ENABLED (plaintext secrets endpoints refused)"
else
  echo "       [note] HTTPS enforcement is off. Once every paired phone is on a"
  echo "              build that speaks HTTPS, enable it with:"
  echo "                sudo touch /etc/greenhouse/require_https && sudo systemctl restart greenhouse-portal"
fi
# The camera token must not be the repo's public placeholder. Absent is fine
# now that the camera is parked and install.sh no longer provisions it -- but
# a leftover placeholder is still a public value sitting on disk, so that case
# stays a failure whether or not the camera is running.
if [ "$(tr -d '[:space:]' < /etc/greenhouse/cam_token.txt 2>/dev/null)" = "your-cam-shared-token" ]; then
  no "cam_token is the PUBLIC placeholder from the repo -- delete it (camera parked) or run rotate_secrets.sh"
elif [ -s /etc/greenhouse/cam_token.txt ]; then
  ok "cam_token is unit-specific"
else
  ok "cam_token not provisioned (camera parked)"
fi
# No credential still in use may be one that leaked into git history, or a
# public placeholder from the repo. This is what makes rotation verifiable
# rather than something you have to remember doing.
if python3 "$(dirname "$0")/check_leaked_secrets.py" >/dev/null 2>&1; then
  ok "no known-compromised credentials in use"
else
  no "COMPROMISED CREDENTIALS IN USE -- run: sudo bash $(dirname "$0")/rotate_secrets.sh"
  python3 "$(dirname "$0")/check_leaked_secrets.py" 2>&1 | sed 's/^/       /' || true
fi
# Security audit trail: present, writable, and summarised so an ongoing attack
# is visible at a glance rather than only in a push notification the owner may
# have missed.
if [ -f /var/log/greenhouse-security.log ]; then
  ok "security audit log present"
  python3 - <<'PYEOF'
import os, sys
sys.path.insert(0, '/home/pi/greenhouse/shared')
try:
    import security_log
    events = security_log.read_recent(limit=500)
    if not events:
        print('       no security events recorded')
    else:
        counts = security_log.summarize(events)
        print(f'       recent security events ({len(events)}):')
        for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            flag = '  <-- investigate' if kind in security_log.ALERTABLE else ''
            print(f'         {n:>5}  {kind}{flag}')
except Exception as e:
    print(f'       (could not summarise: {e})')
PYEOF
else
  no "security audit log missing (run install.sh)"
fi
# SSH: key-only auth is the hardened state.
if grep -rqs '^PasswordAuthentication no' /etc/ssh/sshd_config.d/ /etc/ssh/sshd_config; then
  ok "SSH password authentication disabled"
else
  no "SSH password authentication still enabled (add a key, re-run install.sh)"
fi

echo "== mDNS =="
systemctl is-active avahi-daemon >/dev/null 2>&1 && ok "avahi-daemon running" || no "avahi-daemon not running"

echo "== hardening artifacts =="
[ -f /etc/NetworkManager/dnsmasq-shared.d/greenhouse-captive.conf ] && ok "captive DNS config present" || no "captive DNS config missing"
command -v iptables >/dev/null && ok "iptables present" || no "iptables missing"
[ -f /etc/systemd/system/mosquitto.service.d/greenhouse.conf ] && ok "mosquitto ordering drop-in present" || no "mosquitto drop-in missing"

echo "== AP profile sanity =="
command -v nmcli >/dev/null && ok "nmcli present" || no "nmcli missing"

echo ""
echo "== device credentials (SECRETS -- this output pairs the app; don't paste it publicly) =="
python3 - <<'PYEOF'
import json, sys
try:
    d = json.load(open('/etc/greenhouse/device.json'))
    print(f"       device_id : {d['device_id']}")
    print(f"       username  : {d['username']}")
    print(f"       password  : {d['password']}")
    print(f"       port      : {d['port']}")
    # Needed for pairing: the PIN for the normal discovery flow, the tokens
    # for the manual-entry path (pairing screen -> Advanced) when mDNS
    # discovery can't find the unit.
    print(f"       pair_pin  : {d.get('pair_pin', '(missing)')}")
    print(f"       api_token : {d.get('api_token', '(missing -- run install.sh)')}")
    # Short out-of-band identity check. Must match what the app shows under
    # Settings after pairing; a mismatch means the app is talking to something
    # that is not this Pi (i.e. a man-in-the-middle substituted its own
    # certificate during pairing). Same derivation as
    # app/lib/utils/cert_pinning.dart's safetyCode().
    import hashlib
    fp = d.get('tls_fingerprint', '')
    parts = [p.strip().upper() for p in fp.split(',') if p.strip()]
    if parts:
        h = hashlib.sha256(','.join(parts).encode()).hexdigest()[:8].upper()
        print(f"       SAFETY CODE : {h[:4]}-{h[4:]}   <-- compare with the app")
    else:
        print("       SAFETY CODE : (no certs yet)")
except Exception as e:
    print(f"       error: {e}", file=sys.stderr)
try:
    with open('/etc/greenhouse/cam_token.txt') as f:
        print(f"       cam_token : {f.read().strip() or '(empty)'}")
except Exception:
    print("       cam_token : (not provisioned)")
PYEOF

echo ""
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
