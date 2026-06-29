#!/usr/bin/env python3
"""
Greenhouse portal server — port 80.

AP mode  (no /etc/greenhouse/.wifi_configured):
    GET  /            -> WiFi setup form (browser captive portal)
    GET  /<probe>     -> 302 to / so the OS captive-portal popup fires reliably
    POST /connect     -> save WiFi from the HTML form, reboot
    POST /api/connect -> save WiFi from the Flutter app (JSON), reboot

STA mode (.wifi_configured present):
    GET  /pair        -> pairing JSON consumed by the Flutter app
"""
import json
import os
import subprocess
import time

from flask import Flask, abort, jsonify, redirect, render_template, request

_START_TIME = time.time()
_PAIR_WINDOW = 300  # seconds the /pair endpoint stays open after boot

app = Flask(__name__, template_folder="templates")

_CONFIG = "/etc/greenhouse/device.json"
_WIFI_SENTINEL = "/etc/greenhouse/.wifi_configured"
_CLIENT_CONN = "greenhouse-home"

# OS captive-portal probe paths: returning 302 to "/" causes iOS, Android,
# and Windows to open their built-in captive-portal browser popup reliably.
_PROBE_PATHS = frozenset({
    "hotspot-detect.html",        # Apple iOS / macOS
    "library/test/success.html",  # older Apple
    "generate_204",               # Android / Chrome OS
    "connecttest.txt",            # Windows NCSI
    "ncsi.txt",                   # Windows NCSI fallback
    "redirect",                   # Android generic
    "success.txt",
    "canonical.html",
})


def _ap_mode() -> bool:
    return not os.path.exists(_WIFI_SENTINEL)


def _load_config() -> dict:
    with open(_CONFIG) as f:
        return json.load(f)


def _validate(ssid: str, password: str):
    if not ssid:
        return "Please enter your WiFi name."
    if len(ssid.encode()) > 32:
        return "WiFi name is too long."
    if password and not (8 <= len(password.encode()) <= 63):
        return "WiFi password must be 8-63 characters."
    return None


def _save_wifi(ssid: str, password: str) -> None:
    subprocess.run(["nmcli", "connection", "delete", _CLIENT_CONN],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0",
         "con-name", _CLIENT_CONN, "autoconnect", "yes",
         "connection.autoconnect-priority", "10",
         "ssid", ssid],
        check=True)
    if password:
        subprocess.run(
            ["nmcli", "connection", "modify", _CLIENT_CONN,
             "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password],
            check=True)
    # Disable autoconnect on every other WiFi profile so only greenhouse-home
    # reconnects after reboot (avoids Pi Imager dev-WiFi racing it on boot).
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            capture_output=True, text=True).stdout
        for line in out.splitlines():
            name, _, ctype = line.partition(":")
            if "wireless" in ctype and name != _CLIENT_CONN:
                subprocess.run(
                    ["nmcli", "connection", "modify", name,
                     "connection.autoconnect", "no"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    open(_WIFI_SENTINEL, "w").close()


def _reboot_soon() -> None:
    subprocess.Popen(["bash", "-c", "sleep 3 && reboot"])


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def index(path):
    if _ap_mode():
        filename = path.rstrip("/").rsplit("/", 1)[-1]
        if filename in _PROBE_PATHS and path:
            # Connectivity probe from the phone's OS → redirect to setup page.
            # The redirect is what triggers the captive-portal popup on iOS,
            # Android, and Windows.
            return redirect("/", code=302)
        return render_template("wifi.html")
    return render_template("rebooting.html", ssid="your network")


@app.route("/api/scan")
def scan():
    if not _ap_mode():
        abort(403)
    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan", "ifname", "wlan0"],
            timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
             "device", "wifi", "list", "ifname", "wlan0"],
            capture_output=True, text=True, timeout=10).stdout
        seen, networks = set(), []
        for line in out.splitlines():
            parts = line.rsplit(":", 2)
            if len(parts) < 3:
                continue
            ssid, signal, security = parts[0], parts[1], parts[2]
            ssid = ssid.replace("\\:", ":")
            if not ssid or ssid in seen or ssid.startswith("Greenhouse-"):
                continue
            seen.add(ssid)
            networks.append({
                "ssid": ssid,
                "secured": bool(security.strip()),
                "signal": int(signal) if signal.isdigit() else 0,
            })
        networks.sort(key=lambda x: -x["signal"])
        return jsonify(networks)
    except Exception:
        return jsonify([])


@app.route("/connect", methods=["POST"])
def connect():
    if not _ap_mode():
        abort(403)
    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "").strip()
    error = _validate(ssid, password)
    if error:
        return render_template("wifi.html", error=error), 400
    _save_wifi(ssid, password)
    _reboot_soon()
    return render_template("rebooting.html", ssid=ssid)


@app.route("/api/connect", methods=["POST"])
def api_connect():
    if not _ap_mode():
        return jsonify({"error": "already configured"}), 403
    data = request.get_json(silent=True) or request.form
    ssid = (data.get("ssid") or "").strip()
    password = (data.get("password") or "").strip()
    error = _validate(ssid, password)
    if error:
        return jsonify({"error": error}), 400
    _save_wifi(ssid, password)
    _reboot_soon()
    return jsonify({"status": "connecting", "ssid": ssid})


@app.route("/pair")
def pair():
    if time.time() - _START_TIME > _PAIR_WINDOW:
        return jsonify({"error": "Pairing window expired. Restart the Pi "
                                 "to open a new pairing window."}), 403
    try:
        c = _load_config()
        try:
            ts_ip = subprocess.run(
                ["tailscale", "ip", "-4"],
                capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:
            ts_ip = ""
        return jsonify({
            "host_lan":        "greenhouse.local",
            "host_tailscale":  ts_ip,
            "port":            c["port"],
            "tls_fingerprint": c["tls_fingerprint"],
            "username":        c["username"],
            "password":        c["password"],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=False)
