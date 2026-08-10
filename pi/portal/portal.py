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
import difflib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time

from flask import Flask, abort, g, jsonify, redirect, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
from history_query import query_points
try:
    from security_log import log_security_event
except Exception:  # pragma: no cover - logging must never break the portal
    def log_security_event(*_a, **_kw):
        return {}

_START_TIME = time.time()
_PAIR_WINDOW = 600  # seconds the /pair endpoint stays open after boot

# /pair/confirm brute-force protection — in-memory, resets on service restart.
#
# This was originally a single GLOBAL counter (5 wrong PINs from ANY source
# locked the endpoint for everyone until a restart). That made the security
# control itself a denial-of-service vector: anyone who could reach the portal
# could permanently block the owner from pairing by burning 5 guesses, and on
# a WiFi-less unit "restart the service" means physically power-cycling the Pi.
#
# Now tracked per client IP, so one hostile client locks out only itself. A
# global ceiling still exists as a backstop against a distributed/spoofed-
# source attempt, but it is deliberately much higher than the per-IP limit and
# expires on its own rather than latching until restart.
MAX_PAIR_ATTEMPTS = 5           # per-IP failures before that IP is locked out
PAIR_LOCKOUT_SECONDS = 900      # 15 min, then that IP may try again
MAX_GLOBAL_PAIR_FAILURES = 50   # backstop across all IPs within the window
GLOBAL_PAIR_WINDOW_SECONDS = 900

# ip -> [failure_count, first_failure_monotonic]
_pair_failures: dict[str, list] = {}
# (count, window_start_monotonic) for the all-sources backstop
_global_pair_failures = [0, 0.0]


def _client_ip() -> str:
    """Best-effort client identity for rate limiting.

    No proxy sits in front of this service (it binds the LAN/AP interface
    directly), so remote_addr is the real peer address and X-Forwarded-For is
    deliberately NOT trusted — honouring it would let a caller forge a fresh
    identity per request and bypass the per-IP limit entirely.
    """
    return request.remote_addr or "unknown"


def _pair_lock_remaining(ip: str, now: float) -> float:
    """Seconds this IP stays locked out, 0 when it may attempt a PIN."""
    entry = _pair_failures.get(ip)
    if entry is None:
        return 0.0
    count, first_ts = entry
    elapsed = now - first_ts
    if elapsed >= PAIR_LOCKOUT_SECONDS:
        _pair_failures.pop(ip, None)  # window expired — forgive and reset
        return 0.0
    if count >= MAX_PAIR_ATTEMPTS:
        return PAIR_LOCKOUT_SECONDS - elapsed
    return 0.0


def _global_pair_locked(now: float) -> bool:
    count, window_start = _global_pair_failures
    if now - window_start >= GLOBAL_PAIR_WINDOW_SECONDS:
        _global_pair_failures[0] = 0
        _global_pair_failures[1] = now
        return False
    return count >= MAX_GLOBAL_PAIR_FAILURES


def _record_pair_failure(ip: str, now: float) -> None:
    entry = _pair_failures.get(ip)
    if entry is None or now - entry[1] >= PAIR_LOCKOUT_SECONDS:
        _pair_failures[ip] = [1, now]
    else:
        entry[0] += 1
    if now - _global_pair_failures[1] >= GLOBAL_PAIR_WINDOW_SECONDS:
        _global_pair_failures[0] = 1
        _global_pair_failures[1] = now
    else:
        _global_pair_failures[0] += 1
    # Bound memory: a hostile client cycling source addresses must not be able
    # to grow this dict without limit. Evict entries whose window has expired,
    # then the oldest if still oversized.
    if len(_pair_failures) > 256:
        for stale_ip in [k for k, v in _pair_failures.items()
                         if now - v[1] >= PAIR_LOCKOUT_SECONDS]:
            _pair_failures.pop(stale_ip, None)
        while len(_pair_failures) > 256:
            _pair_failures.pop(min(_pair_failures, key=lambda k: _pair_failures[k][1]), None)


def _clear_pair_failures(ip: str) -> None:
    _pair_failures.pop(ip, None)

app = Flask(__name__, template_folder="templates")

# Every request this portal legitimately handles is tiny (a WiFi form post, a
# 6-digit PIN, a few query params). Capping the body at 64KB means a hostile
# client on the hotspot can't make the process buffer an arbitrarily large
# upload; Flask returns 413 past this without reading the rest.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


@app.before_request
def _make_csp_nonce():
    # Per-response nonce so wifi.html's inline <script> can run under a CSP
    # that still refuses every other inline script (i.e. anything injected).
    g.csp_nonce = secrets.token_urlsafe(16)


@app.context_processor
def _inject_csp_nonce():
    return {"csp_nonce": getattr(g, "csp_nonce", "")}


@app.after_request
def _security_headers(resp):
    """Defense-in-depth headers for the captive-portal pages.

    The portal is plain HTTP by necessity (a self-signed cert would make the
    captive-portal popup fail), so these don't substitute for transport
    security — they just remove the easy extras: MIME sniffing, framing of the
    WiFi form for clickjacking, and referrer leakage of any URL to a network
    the phone reaches next.

    The script-src uses a per-request nonce rather than 'unsafe-inline': the
    one inline script here is ours (wifi.html's network picker), and a nonce
    keeps it working while still blocking any injected inline script.
    style-src does keep 'unsafe-inline' — the page's styling is one inline
    <style> block plus element style attributes, and CSS injection here has no
    meaningful impact on a form that posts to this same origin.
    """
    nonce = getattr(g, "csp_nonce", "")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Cache-Control", "no-store")
    resp.headers.setdefault(
        "Content-Security-Policy",
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
    return resp

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


def _visible_ssids() -> list:
    """SSIDs wlan0 can currently see, most recent scan.

    An empty list means the scan itself failed or returned nothing -- which is
    NOT the same as "the network is absent", so callers must never treat it as
    proof of a bad SSID.
    """
    try:
        subprocess.run(
            ["sudo", "nmcli", "device", "wifi", "rescan", "ifname", "wlan0"],
            timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass  # rescan is best-effort; the cached list below is still usable
    try:
        out = subprocess.run(
            ["sudo", "nmcli", "-t", "-f", "SSID", "device", "wifi", "list",
             "ifname", "wlan0"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    seen = []
    for line in out.splitlines():
        ssid = line.replace("\\:", ":").strip()
        if ssid and ssid not in seen and not ssid.startswith("Greenhouse-"):
            seen.append(ssid)
    return seen


# A typed SSID is matched by NetworkManager byte-for-byte against the SSID
# information element on the air -- there is no fuzzy match and no feedback.
# Before this check, a one-character typo was accepted silently, written into
# the profile, and only surfaced as a mystery reboot back into AP mode minutes
# later, indistinguishable from a wrong password or a dead radio. Catching it
# here turns that whole loop into one inline error. (Cost a full bench day to
# diagnose on 2026-08-10: "billyredmi" typed for a hotspot named "billredmi".)
def _ssid_reachable(ssid: str):
    """None when the SSID is visible (or unprovable). Otherwise (message, list)
    where list is what the unit CAN see, best match first."""
    visible = _visible_ssids()
    if not visible or ssid in visible:
        return None
    ci = [s for s in visible if s.lower() == ssid.lower()]
    if ci:
        return (f'No network named "{ssid}". Did you mean "{ci[0]}"? '
                f"WiFi names are case-sensitive.", visible)
    close = difflib.get_close_matches(ssid, visible, n=3, cutoff=0.6)
    if close:
        return (f'No network named "{ssid}". Did you mean "{close[0]}"?',
                visible)
    return (f'The greenhouse cannot see a network named "{ssid}".', visible)


# The portal runs as `pi`, not root (see IMPROVEMENTS.md finding A2) — nmcli
# and reboot need real privilege, granted narrowly via
# /etc/sudoers.d/greenhouse-portal (see pi/portal/greenhouse-portal.sudoers).
def _save_wifi(ssid: str, password: str, hidden: bool = False) -> None:
    subprocess.run(["sudo", "nmcli", "connection", "delete", _CLIENT_CONN],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["sudo", "nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0",
         "con-name", _CLIENT_CONN, "autoconnect", "yes",
         "connection.autoconnect-priority", "10",
         "ssid", ssid],
        check=True)
    if hidden:
        # A non-broadcasting AP is only found if NM actively probes for it;
        # without this the profile sits there never matching anything. Set only
        # when the operator explicitly overrode the visibility check, since it
        # costs an extra probe request on every scan.
        subprocess.run(
            ["sudo", "nmcli", "connection", "modify", _CLIENT_CONN,
             "802-11-wireless.hidden", "yes"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if password:
        subprocess.run(
            ["sudo", "nmcli", "connection", "modify", _CLIENT_CONN,
             "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password,
             # psk-flags 0 = system-owned. NM's default already is 0, but an
             # agent-owned secret makes boot-time `nmcli connection up` fail
             # with "Secrets were required, but not provided" on a headless
             # unit with no agent running -- assert it rather than inherit it.
             "wifi-sec.psk-flags", "0"],
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
    # A bash-spawned "sleep 3 && sudo reboot" chain was silently dying before
    # ever reaching sudo (bench-tested 2026-08-09 -- no trace of it in the
    # sudo journal at all). Doing the delay in-process with a daemon thread
    # and calling sudo directly (argv list, no shell) matches the pattern
    # that already works for _save_wifi()'s nmcli calls.
    def _delayed_reboot():
        time.sleep(3)
        subprocess.run(["sudo", "reboot"])
    threading.Thread(target=_delayed_reboot, daemon=True).start()


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
    force = request.form.get("force") == "1"
    error = _validate(ssid, password)
    if error:
        return render_template("wifi.html", error=error), 400
    if not force:
        unreachable = _ssid_reachable(ssid)
        if unreachable:
            message, visible = unreachable
            # allow_force lets the operator commit anyway for a hidden SSID.
            return render_template("wifi.html", error=message,
                                   visible=visible, allow_force=True,
                                   typed_ssid=ssid), 400
    _save_wifi(ssid, password, hidden=force)
    _reboot_soon()
    return render_template("rebooting.html", ssid=ssid)


@app.route("/api/connect", methods=["POST"])
def api_connect():
    if not _ap_mode():
        return jsonify({"error": "already configured"}), 403
    data = request.get_json(silent=True) or request.form
    ssid = (data.get("ssid") or "").strip()
    password = (data.get("password") or "").strip()
    force = str(data.get("force", "")).lower() in ("1", "true", "yes")
    error = _validate(ssid, password)
    if error:
        return jsonify({"error": error}), 400
    if not force:
        unreachable = _ssid_reachable(ssid)
        if unreachable:
            message, visible = unreachable
            # ssid_not_found lets the app offer "connect anyway" (hidden SSID)
            # by re-POSTing the same body with force=true.
            return jsonify({"error": message, "code": "ssid_not_found",
                            "visible": visible}), 400
    _save_wifi(ssid, password, hidden=force)
    _reboot_soon()
    return jsonify({"status": "connecting", "ssid": ssid})


def _load_cam_token() -> str:
    """Shared camera token, so the app can reach the camera's /stream directly.

    Same value cam_bridge.py uses; handed out only through the PIN-gated
    pairing response, never over an unauthenticated endpoint.
    """
    try:
        with open("/etc/greenhouse/cam_token.txt") as f:
            return f.read().strip()
    except Exception:
        return ""


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
        # Read-only history API bearer token (see _require_api_token) and the
        # camera's shared token. Both ride this already-PIN-gated response
        # rather than getting their own enrollment step.
        "api_token":       c.get("api_token", ""),
        "cam_token":       _load_cam_token(),
    }


def _require_api_token() -> bool:
    """True when the caller presented this unit's history-API token.

    Accepts `Authorization: Bearer <token>` or `?token=`. Fails closed when no
    token is provisioned: an unprovisioned unit must refuse rather than serve
    its whole history to anyone who asks.
    """
    try:
        expected = _load_config().get("api_token", "")
    except Exception:
        return False
    if not expected:
        return False
    auth = request.headers.get("Authorization", "")
    supplied = auth[7:] if auth.startswith("Bearer ") else request.args.get("token", "")
    return hmac.compare_digest(supplied, expected)


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
    ip = _client_ip()
    now = time.monotonic()

    remaining = _pair_lock_remaining(ip, now)
    if remaining > 0:
        # Per-IP: only this caller is blocked, and only temporarily. The owner
        # on a different device can still pair right now.
        return jsonify({"error": "Too many incorrect PINs from this device. "
                                 f"Try again in {int(remaining // 60) + 1} minute(s).",
                        "retry_after_seconds": int(remaining)}), 429
    if _global_pair_locked(now):
        return jsonify({"error": "Too many incorrect PINs. Try again later."}), 429

    pin = str((request.get_json(silent=True) or {}).get("pin", ""))
    try:
        expected_pin = _load_config()["pair_pin"]
    except Exception as exc:
        print(f"[portal] pair_confirm config error: {exc}", flush=True)
        return jsonify({"error": "pairing unavailable"}), 500
    # Constant-time compare: `!=` on str short-circuits at the first differing
    # character, so response timing leaks a correct prefix. With only 10^6
    # possible PINs that turns a 5-guess lockout into a feasible digit-by-digit
    # attack for anyone who can measure the response time.
    if not hmac.compare_digest(pin, str(expected_pin)):
        _record_pair_failure(ip, now)
        # A single wrong PIN is a typo; hitting the limit is someone grinding.
        # Only the latter is worth the owner's attention.
        entry = _pair_failures.get(ip)
        if entry and entry[0] >= MAX_PAIR_ATTEMPTS:
            log_security_event('pair_lockout',
                               f'{entry[0]} failed PIN attempts', source=ip)
        else:
            log_security_event('pair_pin_failure', 'wrong PIN', source=ip)
        time.sleep(1)  # throttle — slows even the allowed attempts for a script
        return jsonify({"error": "invalid PIN"}), 401
    _clear_pair_failures(ip)
    try:
        return jsonify(_pairing_payload())
    except Exception as exc:
        # Logged locally, not returned: the payload builder reads config paths
        # and would otherwise echo filesystem details to an unauthenticated-
        # until-this-point caller.
        print(f"[portal] pairing payload error: {exc}", flush=True)
        return jsonify({"error": "pairing unavailable"}), 500


@app.route("/api/history/series")
def history_series():
    if not _require_api_token():
        log_security_event('history_auth_failure', '/api/history/series',
                           source=_client_ip())
        return jsonify({"error": "unauthorized"}), 401
    try:
        conn = _history_db()
        rows = conn.execute(
            "SELECT kind, zone, metric FROM series ORDER BY kind, zone, metric").fetchall()
        conn.close()
        return jsonify([{"kind": r[0], "zone": r[1], "metric": r[2]} for r in rows])
    except Exception as exc:
        print(f"[portal] history_series error: {exc}", flush=True)
        return jsonify({"error": "history unavailable"}), 500


@app.route("/api/history")
def history():
    if not _require_api_token():
        log_security_event('history_auth_failure', '/api/history',
                           source=_client_ip())
        return jsonify({"error": "unauthorized"}), 401
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
        print(f"[portal] history query error: {exc}", flush=True)
        return jsonify({"error": "history unavailable"}), 500


# ── HTTPS listener ───────────────────────────────────────────────────────────
# Port 80 must stay plaintext: the captive-portal popup only fires for an
# unencrypted probe redirect, and a self-signed cert there would make the
# phone's OS refuse the page outright — that's the first-boot WiFi setup flow,
# so it can't be broken.
#
# Everything the APP talks to (pairing, history) has no such constraint: the
# app already pins this unit's certificate fingerprint for MQTT, so it can do
# the same here. Serving those endpoints over TLS on 8443 removes passive
# eavesdropping — the PIN, the MQTT credentials and the API/cam tokens no
# longer cross the LAN in the clear during pairing.
HTTPS_PORT = 8443
PORTAL_CERT = "/etc/greenhouse/portal.crt"
PORTAL_KEY = "/etc/greenhouse/portal.key"
# Optional hardening switch (file need only exist). Once every paired app is
# on a build that speaks HTTPS, creating this file makes the plaintext copies
# of the sensitive endpoints refuse to answer, closing the downgrade path.
# Deliberately NOT created by install.sh: enabling it before the phone is
# updated would lock the owner out of their own history/pairing.
REQUIRE_HTTPS_FLAG = "/etc/greenhouse/require_https"


def _https_available() -> bool:
    return os.path.exists(PORTAL_CERT) and os.path.exists(PORTAL_KEY)


def _require_https_enforced() -> bool:
    return os.path.exists(REQUIRE_HTTPS_FLAG)


@app.before_request
def _enforce_https_when_configured():
    """Refuse sensitive endpoints over plaintext once the operator opts in.

    Scoped to the endpoints that carry secrets — the captive-portal pages and
    the existence-check /pair stay reachable over HTTP no matter what, since
    they're needed before the app has any way to reach 8443.
    """
    if not _require_https_enforced() or request.is_secure:
        return None
    sensitive = ("/pair/confirm", "/api/history")
    if any(request.path.startswith(p) for p in sensitive):
        return jsonify({"error": "HTTPS required",
                        "https_port": HTTPS_PORT}), 403
    return None


def _serve_https() -> None:
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(PORTAL_CERT, PORTAL_KEY)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    app.run(host="0.0.0.0", port=HTTPS_PORT, ssl_context=ctx,
            debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    if _https_available():
        threading.Thread(target=_serve_https, daemon=True).start()
        print(f"[portal] HTTPS listener on :{HTTPS_PORT} "
              f"(enforced={_require_https_enforced()})", flush=True)
    else:
        # Not fatal: a unit whose certs haven't been generated yet still needs
        # its captive portal to come up so the owner can finish setup.
        print("[portal] WARN: no portal cert/key — HTTPS listener disabled",
              flush=True)
    app.run(host="0.0.0.0", port=80, debug=False, threaded=True,
            use_reloader=False)
