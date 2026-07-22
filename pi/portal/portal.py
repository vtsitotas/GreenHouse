#!/usr/bin/env python3
"""
Greenhouse portal server — port 80.

AP mode  (no /etc/greenhouse/.wifi_configured):
    GET  /            -> WiFi setup form (browser captive portal)
    GET  /<probe>     -> 302 to / so the OS captive-portal popup fires reliably
    POST /connect     -> save WiFi from the HTML form, reboot
    POST /api/connect -> save WiFi from the Flutter app (JSON), reboot

STA mode (.wifi_configured present):
    GET  /pair          -> {"found": true} — existence check only, no secrets
    POST /pair/confirm  -> {"pin": "123456"} -> pairing JSON consumed by the
                            Flutter app, only if the PIN matches. 5 wrong
                            PINs lock the endpoint until the service restarts.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

from flask import Flask, abort, jsonify, redirect, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
from history_query import query_points

_START_TIME = time.time()
_PAIR_WINDOW = 600  # seconds the /pair endpoint stays open after boot

# /pair/confirm lockout — in-memory, same pattern as _START_TIME. Resets only
# on service restart: this is a small greenhouse LAN/hotspot, not a public
# service, so a global counter (not per-IP) is the simpler, sufficient choice
# (see docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md).
MAX_PAIR_ATTEMPTS = 5
_pair_fail_count = 0
_pair_locked = False

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


def _load_hivemq() -> dict:
    try:
        with open('/etc/greenhouse/hivemq.json') as f:
            return json.load(f)
    except Exception:
        return {}


_RECORDER_DB = "/var/lib/greenhouse/greenhouse.db"


def _history_db() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_RECORDER_DB}?mode=ro", uri=True)


def _validate(ssid: str, password: str):
    if not ssid:
        return "Please enter your WiFi name."
    if len(ssid.encode()) > 32:
        return "WiFi name is too long."
    if password and not (8 <= len(password.encode()) <= 63):
        return "WiFi password must be 8-63 characters."
    return None


# The portal runs as `pi`, not root (see IMPROVEMENTS.md finding A2) — nmcli
# and reboot need real privilege, granted narrowly via
# /etc/sudoers.d/greenhouse-portal (see pi/portal/greenhouse-portal.sudoers).
def _save_wifi(ssid: str, password: str) -> None:
    subprocess.run(["sudo", "nmcli", "connection", "delete", _CLIENT_CONN],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["sudo", "nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0",
         "con-name", _CLIENT_CONN, "autoconnect", "yes",
         "connection.autoconnect-priority", "10",
         "ssid", ssid],
        check=True)
    if password:
        subprocess.run(
            ["sudo", "nmcli", "connection", "modify", _CLIENT_CONN,
             "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password],
            check=True)
    # Disable autoconnect on every other WiFi profile so only greenhouse-home
    # reconnects after reboot (avoids Pi Imager dev-WiFi racing it on boot).
    try:
        out = subprocess.run(
            ["sudo", "nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            capture_output=True, text=True).stdout
        for line in out.splitlines():
            name, _, ctype = line.partition(":")
            if "wireless" in ctype and name != _CLIENT_CONN:
                subprocess.run(
                    ["sudo", "nmcli", "connection", "modify", name,
                     "connection.autoconnect", "no"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    open(_WIFI_SENTINEL, "w").close()


def _reboot_soon() -> None:
    subprocess.Popen(["bash", "-c", "sleep 3 && sudo reboot"])


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
            ["sudo", "nmcli", "device", "wifi", "rescan", "ifname", "wlan0"],
            timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["sudo", "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
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


def _pairing_payload() -> dict:
    c  = _load_config()
    hm = _load_hivemq()
    return {
        "host_lan":        "greenhouse.local",
        "host_remote":     hm.get("host", ""),
        "port":            c["port"],
        "tls_fingerprint": c["tls_fingerprint"],
        "username":        c["username"],
        "password":        c["password"],
        "remote_username": hm.get("username", ""),
        "remote_password": hm.get("password", ""),
    }


@app.route("/pair")
def pair():
    # Existence check only — no secrets. mDNS/DNS-SD has no authenticity
    # guarantee, so anything that could hand out credentials here would be
    # exposed to spoofing; the real handoff now requires the PIN below.
    if time.time() - _START_TIME > _PAIR_WINDOW:
        return jsonify({"error": "Pairing window expired. Restart the Pi "
                                 "to open a new pairing window."}), 403
    return jsonify({"found": True})


@app.route("/pair/confirm", methods=["POST"])
def pair_confirm():
    global _pair_fail_count, _pair_locked
    if _pair_locked:
        return jsonify({"error": "Too many incorrect PINs. Restart the Pi "
                                 "to try again."}), 429
    pin = str((request.get_json(silent=True) or {}).get("pin", ""))
    try:
        expected_pin = _load_config()["pair_pin"]
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    if pin != expected_pin:
        _pair_fail_count += 1
        if _pair_fail_count >= MAX_PAIR_ATTEMPTS:
            _pair_locked = True
        time.sleep(1)  # throttle — slows even the 5 allowed attempts for a script
        return jsonify({"error": "invalid PIN"}), 401
    _pair_fail_count = 0
    try:
        return jsonify(_pairing_payload())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history/series")
def history_series():
    try:
        conn = _history_db()
        rows = conn.execute(
            "SELECT kind, zone, metric FROM series ORDER BY kind, zone, metric").fetchall()
        conn.close()
        return jsonify([{"kind": r[0], "zone": r[1], "metric": r[2]} for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history")
def history():
    zone = request.args.get("zone")
    kind = request.args.get("kind") or ("zone" if zone else None)
    metric = request.args.get("metric")
    since_raw = request.args.get("since")
    until_raw = request.args.get("until")
    try:
        hours = float(request.args.get("hours", 24))
        since = float(since_raw) if since_raw is not None else None
        until = float(until_raw) if until_raw is not None else None
    except ValueError:
        return jsonify({"error": "hours/since/until must be numbers"}), 400
    if not metric or not kind:
        return jsonify({"error": "metric and (zone or kind) are required"}), 400
    if (since is None) != (until is None):
        return jsonify({"error": "since and until must be provided together"}), 400

    try:
        conn = _history_db()
        result = query_points(conn, kind, zone, metric, hours=hours, since=since, until=until)
        conn.close()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=False)
