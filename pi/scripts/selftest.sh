#!/bin/bash
# Verifies a provisioned greenhouse unit without rebooting / dropping SSH.
# Run: sudo bash /home/pi/greenhouse/scripts/selftest.sh
PASS=0; FAIL=0
ok(){ echo "  [ OK ] $1"; PASS=$((PASS+1)); }
no(){ echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "== services enabled =="
for s in greenhouse-firstboot greenhouse-portal greenhouse-ap mosquitto; do
  systemctl is-enabled "$s" >/dev/null 2>&1 && ok "$s enabled" || no "$s not enabled"
done

echo "== mosquitto listeners =="
ss -tlnp 2>/dev/null | grep -q ':8883' && ok "TLS 8883 listening" || no "8883 not listening"
ss -tlnp 2>/dev/null | grep -q ':9001' && ok "WebSocket 9001 listening" || no "9001 not listening"

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

echo "== mqtt round-trip (TLS, authed) =="
USER=$(python3 -c "import json;print(json.load(open('/etc/greenhouse/device.json'))['username'])" 2>/dev/null)
PASS_=$(python3 -c "import json;print(json.load(open('/etc/greenhouse/device.json'))['password'])" 2>/dev/null)
if timeout 10 mosquitto_pub -h greenhouse.local -p 8883 --cafile /etc/mosquitto/certs/ca.crt \
     -u "$USER" -P "$PASS_" -t greenhouse/selftest -m ok 2>/dev/null; then
  ok "authenticated TLS publish works"
else
  no "TLS publish failed"
fi

echo "== portal =="
curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/pair | grep -qE '200|403' && ok "portal responding on 8080" || no "portal not responding"

echo "== AP profile sanity =="
command -v nmcli >/dev/null && ok "nmcli present" || no "nmcli missing"

echo ""
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
