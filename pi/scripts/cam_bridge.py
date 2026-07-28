#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — cam_bridge.py
# Receives periodic snapshots from the ESP32-CAM, runs grayscale frame-diff
# motion detection, tells the camera which frames to keep on its own SD card,
# fires push alerts, and relays event photos / on-demand live frames to the
# app over MQTT. See docs/superpowers/specs/2026-07-10-esp32-cam-integration-design.md.
# ═══════════════════════════════════════════════════════════════════════════
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.request import urlopen

from flask import Flask, request
import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import cam_store
import motion
from push import send_push
try:
    from security_log import log_security_event
except Exception:  # pragma: no cover - logging must never break the intake
    def log_security_event(*_a, **_kw):
        return {}

# ── Config ───────────────────────────────────────────────────────────────────
CAM_HTTP_PORT = 8090
DB_PATH = '/var/lib/greenhouse/cam_events.db'
NOTIFICATION_SETTINGS_CFG = '/etc/greenhouse/notification_settings.json'
MOTION_THRESHOLD = 12.0
EVENT_MAX_AGE_DAYS = 7

# Shared token gating cam_esp32's /capture and /event/<id> (see
# IMPROVEMENTS.md finding A5) -- must match CAM_TOKEN in the camera's
# flashed secrets.h exactly; there's no automatic sync between the two.
CAM_TOKEN_FILE = '/etc/greenhouse/cam_token.txt'


def _load_cam_token() -> str:
    try:
        with open(CAM_TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return ''


CAM_TOKEN = _load_cam_token()

# Whether a plain bearer token (rather than a signature) still authenticates a
# frame POST. Kept on by default so a camera that hasn't been reflashed with
# signing firmware keeps working; create /etc/greenhouse/no_unsigned_cam to
# turn it off once every camera on site is updated. Same opt-in-hardening
# pattern as the portal's /etc/greenhouse/require_https.
ALLOW_UNSIGNED_CAM_AUTH = not os.path.exists('/etc/greenhouse/no_unsigned_cam')


def _cam_url(camera_ip: str, path: str) -> str:
    return f'http://{camera_ip}{path}?token={CAM_TOKEN}'


# Max accepted snapshot body. A VGA JPEG at quality 12 is ~40-60KB; 2MB is
# generous headroom while still bounding what an unauthenticated-until-checked
# request can make this process allocate.
MAX_FRAME_BYTES = 2 * 1024 * 1024


def _token_ok(supplied: str) -> bool:
    """Constant-time check of a caller-supplied token against CAM_TOKEN.

    Fails closed when no token is provisioned: an empty/missing
    /etc/greenhouse/cam_token.txt must not silently degrade into "everyone is
    authorized". install.sh always writes at least the placeholder, so an
    empty value here means genuine misconfiguration, not a normal state.
    """
    if not CAM_TOKEN:
        return False
    return hmac.compare_digest(supplied or '', CAM_TOKEN)


# ── Signed requests (keeps CAM_TOKEN off the wire) ───────────────────────────
# The camera link is plain HTTP: the ESP32 has no practical way to validate a
# per-unit self-signed certificate without firmware-side provisioning. A bearer
# token over that link is readable by any passive sniffer on the LAN, and once
# read it authorizes DELETE /event/<id> on the camera — i.e. destroying stored
# motion evidence.
#
# Signing fixes the part that matters without needing TLS: the camera proves
# knowledge of CAM_TOKEN by sending HMAC-SHA256(token, timestamp + body-hash)
# instead of the token itself. A sniffer sees only a signature, which is bound
# to one timestamp and one exact body, so it can neither recover the token nor
# replay the request. Frame *contents* are still visible — that needs TLS — but
# the credential no longer leaks.
SIGNATURE_WINDOW_SECONDS = 300  # tolerate modest clock skew, bound replay
_seen_signatures: dict = {}     # signature -> monotonic first-seen
_MAX_SEEN_SIGNATURES = 512


def _expected_signature(timestamp: str, body: bytes) -> str:
    msg = timestamp.encode() + b'.' + hashlib.sha256(body).hexdigest().encode()
    return hmac.new(CAM_TOKEN.encode(), msg, hashlib.sha256).hexdigest()


def _replayed(signature: str) -> bool:
    """True if this exact signature was already accepted inside the window."""
    now = time.monotonic()
    for old, seen in list(_seen_signatures.items()):
        if now - seen > SIGNATURE_WINDOW_SECONDS:
            _seen_signatures.pop(old, None)
    if signature in _seen_signatures:
        return True
    if len(_seen_signatures) >= _MAX_SEEN_SIGNATURES:
        _seen_signatures.pop(min(_seen_signatures, key=_seen_signatures.get), None)
    _seen_signatures[signature] = now
    return False


# ── Frame encryption ─────────────────────────────────────────────────────────
# Signing keeps CAM_TOKEN off the wire, but the JPEGs themselves were still
# readable by anyone sniffing the LAN — i.e. a passive observer could watch the
# greenhouse. TLS on the ESP32 would need the Pi's per-unit certificate
# provisioned into firmware; encrypting the payload instead achieves the same
# confidentiality with a key both ends already share.
#
# AES-256-GCM. Key derived from CAM_TOKEN by HMAC (trivially reproducible with
# mbedtls on the ESP32, which has a hardware AES engine). Wire format:
#   body = nonce(12) || ciphertext || tag(16)
# The request signature covers this encrypted body, so authenticity and replay
# protection are unchanged.
#
# cryptography arrives as a firebase-admin dependency (install.sh), so this
# adds nothing new to deploy — but the import is guarded so a unit missing it
# degrades to "reject encrypted frames" rather than crashing on startup.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _AESGCM_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the host's packages
    AESGCM = None
    _AESGCM_AVAILABLE = False

FRAME_KEY_INFO = b'greenhouse-cam-frame-key'
GCM_NONCE_BYTES = 12
GCM_TAG_BYTES = 16

# Refuse plaintext frames entirely. Off by default so a camera that hasn't been
# reflashed with encrypting firmware keeps working; same opt-in pattern as
# require_https and no_unsigned_cam.
REQUIRE_ENCRYPTED_CAM = os.path.exists('/etc/greenhouse/require_encrypted_cam')


def _frame_key() -> bytes:
    """32-byte AES key derived from the shared camera token."""
    return hmac.new(CAM_TOKEN.encode(), FRAME_KEY_INFO, hashlib.sha256).digest()


def _decrypt_frame(blob: bytes):
    """nonce||ciphertext||tag -> plaintext JPEG, or None if it doesn't verify.

    GCM authenticates as it decrypts, so a tampered or wrongly-keyed payload
    fails here rather than reaching the motion detector as garbage.
    """
    if not _AESGCM_AVAILABLE or not CAM_TOKEN:
        return None
    if len(blob) < GCM_NONCE_BYTES + GCM_TAG_BYTES:
        return None
    nonce, sealed = blob[:GCM_NONCE_BYTES], blob[GCM_NONCE_BYTES:]
    try:
        return AESGCM(_frame_key()).decrypt(nonce, sealed, None)
    except Exception:
        return None


def _signature_ok(timestamp: str, signature: str, body: bytes) -> bool:
    """Verify a signed request. Fails closed on anything malformed."""
    if not CAM_TOKEN or not timestamp or not signature:
        return False
    try:
        skew = abs(time.time() - float(timestamp))
    except (TypeError, ValueError):
        return False
    if skew > SIGNATURE_WINDOW_SECONDS:
        return False
    if not hmac.compare_digest(signature, _expected_signature(timestamp, body)):
        return False
    return not _replayed(signature)


def _request_token() -> str:
    """Token from either the X-Cam-Token header or a ?token= query param.

    The camera firmware sends the header (keeps the secret out of any
    intermediate request log that records URLs); the query param is accepted
    too so the same endpoint stays debuggable with plain curl on the bench.
    """
    return request.headers.get('X-Cam-Token') or request.args.get('token', '')


# ── Rate limiting on the frame intake ────────────────────────────────────────
# The real camera POSTs once per SNAPSHOT_INTERVAL_MS (3s). Anything far above
# that is either a malfunction or an attacker, so cap it well above the legit
# rate but well below "unbounded". This bounds two things the token check
# alone doesn't: offline brute-forcing of the token, and a flood of rejected
# requests keeping this single-threaded Flask app busy.
_MAX_FRAMES_PER_WINDOW = 30
_RATE_WINDOW_SECONDS = 10.0
_rate_buckets: dict = {}       # ip -> [count, window_start_monotonic]
_MAX_RATE_BUCKETS = 256


def _rate_limited(ip: str) -> bool:
    """True when this caller has exceeded the intake rate for its window.

    Checked BEFORE the token comparison so that unauthenticated flooding is
    bounded too — the whole point is to not do per-request crypto work on
    behalf of an attacker.
    """
    now = time.monotonic()
    bucket = _rate_buckets.get(ip)
    if bucket is None or now - bucket[1] >= _RATE_WINDOW_SECONDS:
        if len(_rate_buckets) >= _MAX_RATE_BUCKETS:
            for stale in [k for k, v in _rate_buckets.items()
                          if now - v[1] >= _RATE_WINDOW_SECONDS]:
                _rate_buckets.pop(stale, None)
            while len(_rate_buckets) >= _MAX_RATE_BUCKETS:
                _rate_buckets.pop(min(_rate_buckets, key=lambda k: _rate_buckets[k][1]), None)
        _rate_buckets[ip] = [1, now]
        return False
    bucket[0] += 1
    return bucket[0] > _MAX_FRAMES_PER_WINDOW

MQTT_HOST = '127.0.0.1'
MQTT_PORT = 1883
STATUS_TOPIC = 'greenhouse/cam/status'
EVENT_REQUEST_TOPIC = 'greenhouse/cam/event/request'
EVENT_RESPONSE_PREFIX = 'greenhouse/cam/event/response/'
LIVE_START_TOPIC = 'greenhouse/cam/live/start'
LIVE_STOP_TOPIC = 'greenhouse/cam/live/stop'
LIVE_FRAME_TOPIC = 'greenhouse/cam/live/frame'
LIVE_POLL_INTERVAL = 0.7   # seconds between camera /capture polls (~1.4fps)
LIVE_SESSION_TIMEOUT = 120  # seconds without a keep-alive before auto-stopping
CHUNK_SIZE = 3072  # raw bytes per chunk before base64 (~4KB after encoding) —
                    # conservative, comfortably under any broker/HiveMQ Cloud
                    # per-message size limit. New protocol for this feature;
                    # no existing precedent to match.
HEARTBEAT_STALE_SECONDS = 9  # 3x the camera's ~3s snapshot-POST interval

app = Flask(__name__)

# ── Shared state (Flask request thread, MQTT thread, maintenance thread) ────
_state_lock = threading.Lock()
_db_conn = None
_mqtt_client = None
_prev_gray: bytes | None = None
_camera_ip: str | None = None
_last_seen: float = 0.0
_last_event: dict | None = None
_live_thread: threading.Thread | None = None
_live_stop_event = threading.Event()
_live_last_start = 0.0


def _update_heartbeat(remote_addr: str) -> None:
    global _camera_ip, _last_seen
    with _state_lock:
        _camera_ip = remote_addr
        _last_seen = time.time()


def _get_camera_ip() -> str | None:
    with _state_lock:
        return _camera_ip


def _update_last_event(event_id: str) -> None:
    global _last_event
    with _state_lock:
        _last_event = {'event_id': event_id, 'ts': int(time.time())}


def _load_motion_alert_setting() -> bool:
    try:
        with open(NOTIFICATION_SETTINGS_CFG) as f:
            d = json.load(f)
        return bool(d.get('motion_alert', True))
    except Exception:
        return True


def _fire_motion_alert(event_id: str) -> None:
    when = time.strftime('%H:%M', time.localtime())
    message = f'Motion detected — {when}'
    alert = {'type': 'motion', 'message': message, 'severity': 'info', 'event_id': event_id}
    if _mqtt_client is not None:
        try:
            _mqtt_client.publish('greenhouse/weather/alert', json.dumps(alert))
        except Exception as e:
            print(f'[cam_bridge] WARN: alert publish failed: {e}', flush=True)
    if _load_motion_alert_setting():
        send_push('Motion detected', message)


def _publish_chunked(client, topic: str, data: bytes, extra: dict | None = None) -> None:
    chunks = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)] or [b'']
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        payload = {'chunk': i, 'total': total, 'data': base64.b64encode(chunk).decode('ascii')}
        if extra:
            payload.update(extra)
        client.publish(topic, json.dumps(payload))


def publish_status(client) -> None:
    with _state_lock:
        online = _last_seen > 0 and (time.time() - _last_seen) < HEARTBEAT_STALE_SECONDS
        status = {
            'online': online,
            'last_seen': _last_seen or None,
            'ip': _camera_ip,
            'last_event': _last_event,
        }
    client.publish(STATUS_TOPIC, json.dumps(status), retain=True)


def _handle_event_request(client, raw_payload: bytes) -> None:
    try:
        req = json.loads(raw_payload.decode())
        req_id = req['id']
        event_id = req['event_id']
    except Exception:
        return  # malformed request — no id to reply on, nothing sane to do
    camera_ip = _get_camera_ip()
    if camera_ip is None:
        client.publish(EVENT_RESPONSE_PREFIX + req_id, json.dumps({'error': 'camera_unreachable'}))
        return
    try:
        with urlopen(urllib.request.Request(_cam_url(camera_ip, f'/event/{event_id}')), timeout=8) as resp:
            jpeg_bytes = resp.read()
    except Exception as e:
        print(f'[cam_bridge] WARN: event photo fetch failed: {e}', flush=True)
        client.publish(EVENT_RESPONSE_PREFIX + req_id, json.dumps({'error': 'camera_unreachable'}))
        return
    _publish_chunked(client, EVENT_RESPONSE_PREFIX + req_id, jpeg_bytes)


def _start_live_session(client) -> None:
    global _live_thread, _live_stop_event, _live_last_start
    _live_last_start = time.monotonic()
    if _live_thread is not None and _live_thread.is_alive():
        return  # already running — this call was just a keep-alive refresh
    _live_stop_event = threading.Event()
    _live_thread = threading.Thread(target=_live_loop, args=(client, _live_stop_event), daemon=True)
    _live_thread.start()
    print('[cam_bridge] Live session started', flush=True)


def _stop_live_session() -> None:
    if _live_thread is not None:
        _live_stop_event.set()
    print('[cam_bridge] Live session stop requested', flush=True)


def _live_loop(client, stop_event: threading.Event) -> None:
    frame_id = 0
    while not stop_event.is_set():
        if time.monotonic() - _live_last_start > LIVE_SESSION_TIMEOUT:
            print('[cam_bridge] Live session timed out (no keep-alive)', flush=True)
            break
        camera_ip = _get_camera_ip()
        if camera_ip is not None:
            try:
                with urlopen(urllib.request.Request(_cam_url(camera_ip, '/capture')), timeout=5) as resp:
                    jpeg_bytes = resp.read()
                _publish_chunked(client, LIVE_FRAME_TOPIC, jpeg_bytes, extra={'frame_id': frame_id})
                frame_id += 1
            except Exception as e:
                print(f'[cam_bridge] WARN: live frame fetch failed: {e}', flush=True)
        stop_event.wait(LIVE_POLL_INTERVAL)
    print('[cam_bridge] Live session ended', flush=True)


def _prune_expired_events(conn) -> None:
    now = int(time.time())
    for event_id in cam_store.expired_events(conn, now, EVENT_MAX_AGE_DAYS):
        camera_ip = _get_camera_ip()
        if camera_ip is None:
            continue  # retry next cycle once the camera is reachable again
        try:
            urlopen(urllib.request.Request(_cam_url(camera_ip, f'/event/{event_id}'), method='DELETE'),
                    timeout=5)
        except Exception as e:
            print(f'[cam_bridge] WARN: could not delete expired event {event_id} on camera: {e}', flush=True)
            continue  # metadata kept, retry next cycle
        cam_store.delete_event(conn, event_id)


def maintenance_loop(conn, client, interval_seconds: int = 60, _stop_event: threading.Event | None = None) -> None:
    """Runs pruning + status publish once per interval, forever (or until
    _stop_event is set — used only by tests to bound the loop)."""
    stop_event = _stop_event or threading.Event()
    while not stop_event.wait(interval_seconds):
        try:
            _prune_expired_events(conn)
        except Exception as e:
            print(f'[cam_bridge] ERROR: pruning failed: {e}', flush=True)
        try:
            publish_status(client)
        except Exception as e:
            print(f'[cam_bridge] ERROR: status publish failed: {e}', flush=True)


def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe(EVENT_REQUEST_TOPIC)
    client.subscribe(LIVE_START_TOPIC)
    client.subscribe(LIVE_STOP_TOPIC)
    print('[cam_bridge] Connected, subscribed to event/live topics', flush=True)
    publish_status(client)


def on_message(client, userdata, msg):
    if msg.topic == EVENT_REQUEST_TOPIC:
        _handle_event_request(client, msg.payload)
    elif msg.topic == LIVE_START_TOPIC:
        _start_live_session(client)
    elif msg.topic == LIVE_STOP_TOPIC:
        _stop_live_session()


@app.route('/cam/frame', methods=['POST'])
def cam_frame():
    global _prev_gray
    # Authenticate BEFORE anything else — this endpoint used to be completely
    # open to the LAN, which was the most serious hole in the system:
    #   1. _update_heartbeat() below takes the caller's IP as "the camera", so
    #      any host that POSTed here became the camera as far as the Pi was
    #      concerned;
    #   2. every subsequent /capture and /event/<id> fetch then went to that
    #      attacker-controlled address WITH ?token=CAM_TOKEN attached —
    #      handing over the very token that gates DELETE /event/<id> on the
    #      real camera (i.e. the ability to destroy stored motion evidence);
    #   3. the attacker's own images got relayed to the app as live/event
    #      frames, and each forged frame could trigger a motion event, a push
    #      notification, and a DB row.
    # One unauthenticated POST was therefore enough to hijack the camera feed,
    # steal the token, and spam alerts. Same shared secret the Pi already uses
    # in the other direction, so this closes the loop with no new key material.
    if _rate_limited(request.remote_addr or 'unknown'):
        return 'rate limited', 429
    if request.content_length is not None and request.content_length > MAX_FRAME_BYTES:
        return 'too large', 413
    raw = request.get_data()
    if len(raw) > MAX_FRAME_BYTES:
        return 'too large', 413

    # Preferred: a signed request, so CAM_TOKEN itself never crosses the wire
    # (this link has no TLS — see the _signature_ok docs). Falls back to the
    # bearer token for a camera not yet reflashed with signing firmware; that
    # path is equally *authorized* but leaks the token to a passive sniffer,
    # so ALLOW_UNSIGNED_CAM_AUTH exists to switch it off once every camera on
    # site is updated, exactly like the portal's require_https flag.
    signature = request.headers.get('X-Cam-Signature', '')
    timestamp = request.headers.get('X-Cam-Timestamp', '')
    if signature or timestamp:
        if not _signature_ok(timestamp, signature, raw):
            log_security_event('cam_auth_failure', 'bad/replayed signature',
                               source=request.remote_addr or 'unknown')
            return 'unauthorized', 401
    elif ALLOW_UNSIGNED_CAM_AUTH:
        if not _token_ok(_request_token()):
            log_security_event('cam_auth_failure', 'bad token',
                               source=request.remote_addr or 'unknown')
            return 'unauthorized', 401
    else:
        log_security_event('cam_auth_failure', 'unsigned frame refused',
                           source=request.remote_addr or 'unknown')
        return 'unauthorized', 401

    # Decrypt if the camera encrypted the payload. Done AFTER signature
    # verification so an unauthenticated caller can't make this process do
    # crypto work, and before anything looks at the bytes as an image.
    if request.headers.get('X-Cam-Encrypted', '').lower() == 'aes-256-gcm':
        if not _AESGCM_AVAILABLE:
            print('[cam_bridge] ERROR: encrypted frame but no AES support '
                  '(pip install cryptography)', flush=True)
            return 'encryption unsupported', 503
        decrypted = _decrypt_frame(raw)
        if decrypted is None:
            log_security_event('cam_auth_failure', 'frame failed GCM auth',
                               source=request.remote_addr or 'unknown')
            return 'unauthorized', 401   # failed GCM auth = tampered or wrong key
        raw = decrypted
    elif REQUIRE_ENCRYPTED_CAM:
        return 'encryption required', 401

    if not raw:
        return 'discard', 200
    _update_heartbeat(request.remote_addr)
    try:
        gray = motion.downscale_grayscale(raw)
    except Exception as e:
        print(f'[cam_bridge] WARN: bad frame, discarding: {e}', flush=True)
        return 'discard', 200
    with _state_lock:
        prev = _prev_gray
        _prev_gray = gray
    score = motion.diff_score(prev, gray) if prev is not None else 0.0
    if motion.is_motion(score, MOTION_THRESHOLD):
        event_id = f'evt{int(time.time() * 1000)}'
        cam_store.record_event(_db_conn, event_id, int(time.time()), score)
        _update_last_event(event_id)
        _fire_motion_alert(event_id)
        return f'save:{event_id}', 200
    return 'discard', 200


def run():
    global _db_conn, _mqtt_client
    _db_conn = cam_store.init_db(DB_PATH)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-cam-bridge')
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    _mqtt_client = client

    maint_thread = threading.Thread(
        target=maintenance_loop, args=(_db_conn, client), daemon=True)
    maint_thread.start()

    print(f'[cam_bridge] Starting HTTP server on port {CAM_HTTP_PORT}', flush=True)
    app.run(host='0.0.0.0', port=CAM_HTTP_PORT, debug=False)


if __name__ == '__main__':
    run()
