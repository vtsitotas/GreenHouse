#!/bin/bash
# Verifies a provisioned greenhouse unit without rebooting / dropping SSH.
# Run: sudo bash /home/pi/greenhouse/scripts/selftest.sh
PASS=0; FAIL=0
ok(){ echo "  [ OK ] $1"; PASS=$((PASS+1)); }
no(){ echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "== services enabled =="
for s in greenhouse-firstboot greenhouse-portal greenhouse-ap greenhouse-wifi-watchdog greenhouse-recorder greenhouse-hivemq-bridge greenhouse-weather mosquitto; do
  systemctl is-enabled "$s" >/dev/null 2>&1 && ok "$s enabled" || no "$s not enabled"
done

echo "== services active =="
for s in greenhouse-portal greenhouse-recorder greenhouse-hivemq-bridge greenhouse-weather mosquitto; do
  systemctl is-active "$s" >/dev/null 2>&1 && ok "$s running" || no "$s not running"
done

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
curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:80/api/history/series | grep -q '200' && ok "history endpoint responding" || no "history endpoint not responding"

echo "== mDNS =="
systemctl is-active avahi-daemon >/dev/null 2>&1 && ok "avahi-daemon running" || no "avahi-daemon not running"

echo "== hardening artifacts =="
[ -f /etc/NetworkManager/dnsmasq-shared.d/greenhouse-captive.conf ] && ok "captive DNS config present" || no "captive DNS config missing"
command -v iptables >/dev/null && ok "iptables present" || no "iptables missing"
[ -f /etc/systemd/system/mosquitto.service.d/greenhouse.conf ] && ok "mosquitto ordering drop-in present" || no "mosquitto drop-in missing"

echo "== AP profile sanity =="
command -v nmcli >/dev/null && ok "nmcli present" || no "nmcli missing"

echo ""
echo "== device credentials =="
python3 - <<'PYEOF'
import json, sys
try:
    d = json.load(open('/etc/greenhouse/device.json'))
    print(f"       device_id : {d['device_id']}")
    print(f"       username  : {d['username']}")
    print(f"       password  : {d['password']}")
    print(f"       port      : {d['port']}")
except Exception as e:
    print(f"       error: {e}", file=sys.stderr)
PYEOF

echo ""
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
