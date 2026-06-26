#!/usr/bin/env python3
"""
Greenhouse portal server — port 8080.
AP mode:  serves WiFi credentials form at /
STA mode: serves pairing JSON at /pair
"""
import json
import os
import subprocess
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder="templates")

_CONFIG = "/etc/greenhouse/device.json"
_WIFI_SENTINEL = "/etc/greenhouse/.wifi_configured"
_WPA_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"


def _ap_mode() -> bool:
    return not os.path.exists(_WIFI_SENTINEL)


def _load_config() -> dict:
    with open(_CONFIG) as f:
        return json.load(f)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def index(path):
    if _ap_mode():
        return render_template("wifi.html")
    config = _load_config()
    return render_template("rebooting.html", ssid="your network", config=config)


@app.route("/connect", methods=["POST"])
def connect():
    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "").strip()
    if not ssid:
        return render_template("wifi.html", error="Please enter your WiFi name."), 400

    wpa = (
        "country=GR\n"
        "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
        "update_config=1\n\n"
        "network={\n"
        f'    ssid="{ssid}"\n'
        f'    psk="{password}"\n'
        "    key_mgmt=WPA-PSK\n"
        "}\n"
    )
    with open(_WPA_CONF, "w") as f:
        f.write(wpa)

    open(_WIFI_SENTINEL, "w").close()

    # Reboot after 2-second delay so response reaches the browser
    subprocess.Popen(["bash", "-c", "sleep 2 && reboot"])
    return render_template("rebooting.html", ssid=ssid, config=None)


@app.route("/pair")
def pair():
    """Returns pairing JSON consumed by the Greenhouse Flutter app."""
    try:
        c = _load_config()
        return jsonify({
            "host_lan":        "pi.local",
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
