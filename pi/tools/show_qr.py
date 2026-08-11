#!/usr/bin/env python3
"""
Prints the pairing QR code for the Flutter app.

Reads the exact same files portal.py's _pairing_payload() reads
(/etc/greenhouse/device.json, hivemq.json, cam_token.txt) so this can never
drift out of sync with what /pair/confirm actually hands out -- the previous
version took --pass/--user/--port/--tls-fingerprint as manual CLI args and
computed the fingerprint itself via `openssl x509` against a single cert
file, which silently went stale twice: it never gained api_token/cam_token
after the 2026-07-28 security pass added them to the real pairing payload,
and openssl against one file only ever produces one fingerprint, not the
comma-separated CA+leaf pair device.json actually stores (see 79e37ff --
the self-signed chain triggers onBadCertificate for both the CA and the
leaf). A QR from the old script paired successfully but with an empty
api_token (reproducing the history_auth_failure false-alert bug) and a
fingerprint the app's pinning would never match.

Usage: python3 show_qr.py [--lan 192.168.1.42]
Run on the Pi itself, as the `pi` user (no sudo needed -- device.json and
cam_token.txt are root:pi 640, group-readable by first_boot.sh's own chmod).
--lan overrides IP auto-detection (only needed if the Pi has more than one
active network interface and the wrong one gets picked).
"""
import argparse
import json
import socket

import qrcode

DEVICE_CFG = "/etc/greenhouse/device.json"
HIVEMQ_CFG = "/etc/greenhouse/hivemq.json"
CAM_TOKEN_FILE = "/etc/greenhouse/cam_token.txt"


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _load_cam_token() -> str:
    try:
        with open(CAM_TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


def _detect_lan_ip() -> str:
    """The IP the Pi would use to reach the outside world, per the kernel's
    own routing table -- no packet is actually sent (UDP connect() on a
    connectionless socket just picks a source address), so this works even
    with no real internet route, e.g. on a phone hotspot with no data plan
    pass-through. Falls back to the loopback address (visibly wrong, easy to
    spot) if the OS can't resolve a route at all rather than raising."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def build_payload(lan_override: str | None = None) -> dict:
    c = _load_json(DEVICE_CFG)
    hm = _load_json(HIVEMQ_CFG)
    return {
        "host_lan":        lan_override or _detect_lan_ip(),
        "host_remote":     hm.get("host", ""),
        "port":            c.get("port", 8883),
        "tls_fingerprint": c.get("tls_fingerprint", ""),
        "username":        c.get("username", "app"),
        "password":        c.get("password", ""),
        "remote_username": hm.get("username", ""),
        "remote_password": hm.get("password", ""),
        "api_token":       c.get("api_token", ""),
        "cam_token":       _load_cam_token(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", default=None, help="Override auto-detected LAN IP")
    args = ap.parse_args()

    payload = build_payload(args.lan)

    missing = [k for k in ("tls_fingerprint", "password") if not payload[k]]
    if missing:
        print(f"WARNING: {', '.join(missing)} empty in {DEVICE_CFG} -- "
              "this unit may not have finished first-boot provisioning yet.")

    data = json.dumps(payload)
    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    try:
        # print_ascii() writes Unicode block characters -- fine on Raspberry
        # Pi OS's default UTF-8 locale, but a non-UTF-8 terminal encoding
        # (seen testing this from a Windows shell) raises UnicodeEncodeError.
        # The QR is only ever a convenience over the JSON fallback below, so
        # a broken terminal must not stop this script from printing something
        # scannable/usable -- degrade instead of crashing with no output.
        qr.print_ascii(invert=True)
    except UnicodeEncodeError:
        print("(terminal can't render the QR's block characters -- "
              "use the JSON below with the app's manual-entry fields instead)")
    print(f"\nLAN IP used: {payload['host_lan']}"
          + (" (auto-detected)" if not args.lan else " (--lan override)"))
    print("\n--- JSON if QR won't scan ---")
    print(data)


if __name__ == "__main__":
    main()
