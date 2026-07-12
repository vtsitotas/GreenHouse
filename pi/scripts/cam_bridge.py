#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — cam_bridge.py
# Receives periodic snapshots from the ESP32-CAM, runs grayscale frame-diff
# motion detection, tells the camera which frames to keep on its own SD card,
# fires push alerts, and relays event photos / on-demand live frames to the
# app over MQTT. See docs/superpowers/specs/2026-07-10-esp32-cam-integration-design.md.
# ═══════════════════════════════════════════════════════════════════════════
import base64
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

# ── Config ───────────────────────────────────────────────────────────────────
CAM_HTTP_PORT = 8090
DB_PATH = '/var/lib/greenhouse/cam_events.db'
NOTIFICATION_SETTINGS_CFG = '/etc/greenhouse/notification_settings.json'
MOTION_THRESHOLD = 12.0

MQTT_HOST = '127.0.0.1'
MQTT_PORT = 1883
STATUS_TOPIC = 'greenhouse/cam/status'
EVENT_REQUEST_TOPIC = 'greenhouse/cam/event/request'
EVENT_RESPONSE_PREFIX = 'greenhouse/cam/event/response/'
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
        with urlopen(urllib.request.Request(f'http://{camera_ip}/event/{event_id}'), timeout=8) as resp:
            jpeg_bytes = resp.read()
    except Exception as e:
        print(f'[cam_bridge] WARN: event photo fetch failed: {e}', flush=True)
        client.publish(EVENT_RESPONSE_PREFIX + req_id, json.dumps({'error': 'camera_unreachable'}))
        return
    _publish_chunked(client, EVENT_RESPONSE_PREFIX + req_id, jpeg_bytes)


def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe(EVENT_REQUEST_TOPIC)
    print('[cam_bridge] Connected, subscribed to event-request topic', flush=True)
    publish_status(client)


def on_message(client, userdata, msg):
    if msg.topic == EVENT_REQUEST_TOPIC:
        _handle_event_request(client, msg.payload)


@app.route('/cam/frame', methods=['POST'])
def cam_frame():
    global _prev_gray
    raw = request.get_data()
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

    print(f'[cam_bridge] Starting HTTP server on port {CAM_HTTP_PORT}', flush=True)
    app.run(host='0.0.0.0', port=CAM_HTTP_PORT, debug=False)


if __name__ == '__main__':
    run()
