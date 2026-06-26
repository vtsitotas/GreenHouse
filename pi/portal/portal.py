#!/usr/bin/env python3
"""
Greenhouse portal server — port 8080.

AP mode  (no /etc/greenhouse/.wifi_configured):
    GET  /            -> WiFi setup form (browser captive portal)
    POST /connect     -> save WiFi from the HTML form, reboot
    POST /api/connect -> save WiFi from the Flutter app (JSON), reboot

STA mode (.wifi_configured present):
    GET  /pair        -> pairing JSON consumed by the Flutter app
"""
import json
import os
import subprocess
import time

from flask import Flask, abort, jsonify, render_template, request

_START_TIME = time.time()
_PAIR_WINDOW = 300  # seconds the /pair endpoint stays open after boot

app = Flask(__name__, template_folder="templates")

_CONFIG = "/etc/greenhouse/device.json"
_WIFI_SENTINEL = "/etc/greenhouse/.wifi_configured"
_CLIENT_CONN = "greenhouse-home"


def _ap_mode() -> bool:
    return not os.path.exists(_WIFI_SENTINEL)


def _load_config() -> dict:
    with open(_CONFIG) as f:
        return json.load(f)


def _validate(ssid: str, password: str):
    """Returns an error string, or None if the credentials are acceptable."""
    if not ssid:
        return "Please enter your WiFi name."
    if len(ssid.encode()) > 32:
        return "WiFi name is too long."
    # WPA-PSK passphrases are 8-63 chars; empty means an open network.
    if password and not (8 <= len(password.encode()) <= 63):
        return "WiFi password must be 8-63 characters."
    return None


def _save_wifi(ssid: str, password: str) -> None:
    """Create a NetworkManager client profile for the home WiFi and mark
    the unit configured. Credentials are passed as argv (no shell), so no
    escaping/injection concerns. Activation happens on the next boot."""
    subprocess.run(["nmcli", "connection", "delete", _CLIENT_CONN],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0",
         "con-name", _CLIENT_CONN, "autoconnect", "yes", "ssid", ssid],
        check=True)
    if password:
        subprocess.run(
            ["nmcli", "connection", "modify", _CLIENT_CONN,
             "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password],
            check=True)
    # Marks the unit as configured: greenhouse-ap.service skips on next boot.
    open(_WIFI_SENTINEL, "w").close()


def _reboot_soon() -> None:
    # Delay so the HTTP response reaches the client before the radio drops.
    subprocess.Popen(["bash", "-c", "sleep 2 && reboot"])


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def index(path):
    if _ap_mode():
        return render_template("wifi.html")
    return render_template("rebooting.html", ssid="your network", config=None)


@app.route("/connect", methods=["POST"])
def connect():
    """Browser form submission."""
    if not _ap_mode():
        abort(403)
    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "").strip()
    error = _validate(ssid, password)
    if error:
        return render_template("wifi.html", error=error), 400
    _save_wifi(ssid, password)
    _reboot_soon()
    return render_template("rebooting.html", ssid=ssid, config=None)


@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Flutter app submission (JSON: {"ssid": ..., "password": ...})."""
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
    """Returns pairing JSON consumed by the Greenhouse Flutter app."""
    if time.time() - _START_TIME > _PAIR_WINDOW:
        return jsonify({"error": "Pairing window expired. Restart the Pi "
                                 "to open a new pairing window."}), 403
    try:
        c = _load_config()
        return jsonify({
            "host_lan":        "greenhouse.local",
            "host_tailscale":  "",
            "port":            c["port"],
            "tls_fingerprint": c["tls_fingerprint"],
            "username":        c["username"],
            "password":        c["password"],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
