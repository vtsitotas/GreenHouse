#!/bin/bash
# Generates a UNIQUE self-signed CA + server certificate for THIS unit, if absent.
# Called by install.sh (master) and first_boot.sh (each cloned unit's first boot),
# so every shipped Pi has its own key material.
set -e
CERTS=/etc/mosquitto/certs

if [ ! -f "$CERTS/server.crt" ]; then
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
  echo "[gen_certs] generated unique CA + server cert in $CERTS"
fi

# Portal-readable copy of the SAME key pair, checked independently of the
# mosquitto cert above. greenhouse-portal runs as `pi` and can't read the
# mosquitto-owned key, but it needs one to serve HTTPS on 8443. Reusing the
# same cert (rather than minting a second one) means the fingerprint the app
# already pins for MQTT covers the HTTPS portal too -- one identity per unit,
# one thing to verify.
#
# This copy used to live inside the block above, gated on the SAME guard as
# mosquitto's cert -- so a unit provisioned before this step existed, whose
# mosquitto cert already existed on a later install.sh run, would exit before
# ever reaching it and permanently never get a portal keypair (bench-found
# 2026-08-10: "portal TLS keypair missing" on a unit with server.crt already
# present). Checking for the portal copy on its own makes this self-healing on
# any unit that already has a mosquitto cert but is missing it.
if [ ! -f /etc/greenhouse/portal.crt ] || [ ! -f /etc/greenhouse/portal.key ]; then
  mkdir -p /etc/greenhouse
  cp "$CERTS/server.crt" /etc/greenhouse/portal.crt
  cp "$CERTS/server.key" /etc/greenhouse/portal.key
  chown root:pi /etc/greenhouse/portal.crt /etc/greenhouse/portal.key
  chmod 644 /etc/greenhouse/portal.crt
  chmod 640 /etc/greenhouse/portal.key
  echo "[gen_certs] portal HTTPS keypair written to /etc/greenhouse/portal.{crt,key}"
fi
