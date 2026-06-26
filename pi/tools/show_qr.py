#!/usr/bin/env python3
"""
Prints the pairing QR code for the Flutter app.
Usage: python3 show_qr.py --tailscale 100.x.y.z --pass YOUR_PASSWORD
"""
import argparse
import json
import subprocess
import qrcode

def fingerprint():
    r = subprocess.run(
        ["openssl", "x509", "-fingerprint", "-sha256", "-noout",
         "-in", "/home/pi/greenhouse/certs/server.crt"],
        capture_output=True, text=True,
    )
    return r.stdout.strip().split("=", 1)[-1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tailscale", default="")
    ap.add_argument("--user", default="app")
    ap.add_argument("--pass", dest="password", required=True)
    ap.add_argument("--port", type=int, default=8883)
    ap.add_argument("--lan", default="192.168.1.88")
    args = ap.parse_args()

    payload = json.dumps({
        "host_lan":        args.lan,
        "host_tailscale":  args.tailscale,
        "port":            args.port,
        "tls_fingerprint": fingerprint(),
        "username":        args.user,
        "password":        args.password,
    })

    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print("\n--- JSON if QR won't scan ---")
    print(payload)

if __name__ == "__main__":
    main()
