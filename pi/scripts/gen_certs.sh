#!/bin/bash
# Generates a UNIQUE self-signed CA + server certificate for THIS unit, if absent.
# Called by install.sh (master) and first_boot.sh (each cloned unit's first boot),
# so every shipped Pi has its own key material.
set -e
CERTS=/etc/mosquitto/certs
[ -f "$CERTS/server.crt" ] && exit 0

mkdir -p "$CERTS"
openssl genrsa -out "$CERTS/ca.key" 2048
openssl req -new -x509 -days 3650 -key "$CERTS/ca.key" -out "$CERTS/ca.crt" \
  -subj "/CN=GreenhouseCA"
openssl genrsa -out "$CERTS/server.key" 2048
openssl req -new -key "$CERTS/server.key" -out "$CERTS/server.csr" \
  -subj "/CN=greenhouse.local"
openssl x509 -req -days 3650 -in "$CERTS/server.csr" \
  -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
  -out "$CERTS/server.crt"
rm -f "$CERTS/server.csr"

chown -R mosquitto:mosquitto "$CERTS"
chmod 640 "$CERTS"/*.key
chmod 644 "$CERTS"/*.crt

# Portal-readable copy of the SAME key pair. greenhouse-portal runs as `pi`
# and can't read the mosquitto-owned key, but it needs one to serve HTTPS on
# 8443. Reusing the same cert (rather than minting a second one) means the
# fingerprint the app already pins for MQTT covers the HTTPS portal too --
# one identity per unit, one thing to verify.
mkdir -p /etc/greenhouse
cp "$CERTS/server.crt" /etc/greenhouse/portal.crt
cp "$CERTS/server.key" /etc/greenhouse/portal.key
chown root:pi /etc/greenhouse/portal.crt /etc/greenhouse/portal.key
chmod 644 /etc/greenhouse/portal.crt
chmod 640 /etc/greenhouse/portal.key

echo "[gen_certs] generated unique CA + server cert in $CERTS"
echo "[gen_certs] portal HTTPS keypair written to /etc/greenhouse/portal.{crt,key}"
