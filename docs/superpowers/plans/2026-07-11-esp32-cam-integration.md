# ESP32-CAM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single ESP32-CAM to the greenhouse — LAN live view, Pi-side motion detection with push-notification alerts (fetch-on-tap photo), an on-demand MQTT-relayed remote "live" fallback, and a camera online/offline status card.

**Architecture:** The camera firmware stays close to the stock ESP32-CAM web-server example (MJPEG `/stream` + single-frame `/capture`, unchanged for LAN viewing) and gains a periodic snapshot POST to a new Pi service, `pi/scripts/cam_bridge.py`. That service does grayscale frame-diffing to detect motion, tells the camera which frames to keep on its own SD card (the camera is the photo store, not the Pi), fires alerts through the existing `push.send_push()` pipeline, and relays event photos / on-demand live frames to the app over MQTT (chunked, since JPEGs don't fit in one small message). The app gains a Camera screen that switches between a direct LAN stream and the MQTT relay based on the existing `ConnectionStatus.local`/`.remote` signal.

**Tech Stack:** Python 3 (Flask + paho-mqtt + Pillow) on the Pi; Arduino/C++ (`esp_camera.h`, `WebServer.h`, `HTTPClient.h`, `SD_MMC.h`) on the ESP32-CAM; Flutter/Riverpod on the app (new `flutter_mjpeg` dependency for the LAN stream widget).

## Global Constraints

- MQTT topics use the existing `greenhouse/` namespace and the project's established retained-topic-for-Pi-poll / per-request-id-response patterns (see `pi/scripts/weather.py`, `pi/scripts/recorder.py`, `app/lib/repository/greenhouse_repository.dart`). Do not invent a different sync mechanism.
- Binary photo/frame payloads are chunked: `{"chunk": i, "total": N, "data": "<base64>"}`, `CHUNK_SIZE = 3072` raw bytes per chunk (~4KB after base64) — no existing precedent in this codebase carries binary data over MQTT, so this chunk envelope is the new, from-scratch protocol for this feature. Keep it exactly this shape in every producer/consumer.
- Motion detection runs on the Pi, never on the camera (approved design decision — the camera stays close to a stock sketch).
- Event photos live on the camera's SD card; the Pi stores only lightweight metadata (`event_id`, timestamp, diff score) and fetches the JPEG from the camera on demand.
- Remote live view is strictly on-demand (only while the app's Camera screen is open) — never continuous background polling.
- Retention: 7 days, age-based, driven by the Pi (the camera needs no RTC/NTP of its own).
- Firmware changes (Task 8) cannot be compiled or bench-tested in this environment — same situation as the existing mesh-relay firmware. That task's "test cycle" is a manual flash-and-bench-test checklist, not automated tests.
- Follow this project's existing conventions exactly: Python services are flat scripts with module-level globals (not classes), matching `weather.py`/`recorder.py`; Dart follows the existing Riverpod provider → repository → connection layering.

---

## Task 1: Motion-detection scoring (`pi/shared/motion.py`)

**Files:**
- Create: `pi/shared/motion.py`
- Test: `pi/tests/test_motion.py`

**Interfaces:**
- Produces: `motion.downscale_grayscale(jpeg_bytes: bytes, size: tuple[int,int] = (80, 60)) -> bytes`, `motion.diff_score(prev: bytes | None, curr: bytes) -> float`, `motion.is_motion(score: float, threshold: float = 12.0) -> bool`. Task 3 imports all three.

- [ ] **Step 1: Write the failing tests**

```python
# pi/tests/test_motion.py
import io
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import motion


def _solid_jpeg(color: int, size=(320, 240)) -> bytes:
    img = Image.new('L', size, color=color).convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return buf.getvalue()


def test_downscale_grayscale_returns_expected_byte_length():
    jpeg = _solid_jpeg(128)
    gray = motion.downscale_grayscale(jpeg, size=(80, 60))
    assert len(gray) == 80 * 60


def test_diff_score_zero_for_identical_frames():
    jpeg = _solid_jpeg(100)
    gray = motion.downscale_grayscale(jpeg, size=(80, 60))
    assert motion.diff_score(gray, gray) == 0.0


def test_diff_score_high_for_very_different_frames():
    dark = motion.downscale_grayscale(_solid_jpeg(10), size=(80, 60))
    bright = motion.downscale_grayscale(_solid_jpeg(250), size=(80, 60))
    assert motion.diff_score(dark, bright) > 100.0


def test_diff_score_returns_zero_when_prev_is_none():
    curr = motion.downscale_grayscale(_solid_jpeg(50), size=(80, 60))
    assert motion.diff_score(None, curr) == 0.0


def test_is_motion_true_above_threshold():
    assert motion.is_motion(20.0, threshold=12.0) is True


def test_is_motion_false_below_threshold():
    assert motion.is_motion(5.0, threshold=12.0) is False


def test_is_motion_true_at_exact_threshold():
    assert motion.is_motion(12.0, threshold=12.0) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pi/tests/test_motion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'motion'`

- [ ] **Step 3: Write the implementation**

```python
# pi/shared/motion.py
"""Grayscale-downscale frame diffing for camera motion detection.

Deliberately simple (mean absolute pixel difference over a small downscaled
grayscale frame) rather than any real computer-vision library — this project
runs on a Pi Zero W and only needs to answer "did something change enough to
be worth an alert," not track objects or classify motion.
"""
from io import BytesIO

from PIL import Image


def downscale_grayscale(jpeg_bytes: bytes, size: tuple[int, int] = (80, 60)) -> bytes:
    """Decode a JPEG and return its raw grayscale pixel bytes at `size`."""
    img = Image.open(BytesIO(jpeg_bytes)).convert('L').resize(size)
    return img.tobytes()


def diff_score(prev: bytes | None, curr: bytes) -> float:
    """Mean absolute pixel difference between two same-sized grayscale frames.

    Returns 0.0 (no motion) when there's no previous frame yet, or if the
    frame sizes don't match (e.g. right after a resolution change).
    """
    if prev is None or len(prev) != len(curr):
        return 0.0
    total = sum(abs(p - c) for p, c in zip(prev, curr))
    return total / len(curr)


def is_motion(score: float, threshold: float = 12.0) -> bool:
    return score >= threshold
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pi/tests/test_motion.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add pi/shared/motion.py pi/tests/test_motion.py
git commit -m "feat: add grayscale frame-diff motion scoring for the camera bridge"
```

---

## Task 2: Event metadata store (`pi/shared/cam_store.py`)

**Files:**
- Create: `pi/shared/cam_store.py`
- Test: `pi/tests/test_cam_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `cam_store.init_db(db_path: str) -> sqlite3.Connection`, `cam_store.record_event(conn, event_id: str, ts: int, diff_score: float) -> None`, `cam_store.expired_events(conn, now: int, max_age_days: int = 7) -> list[str]`, `cam_store.delete_event(conn, event_id: str) -> None`, `cam_store.latest_event(conn) -> dict | None` (`{'event_id': str, 'ts': int}`). Task 3/6 import all five.

- [ ] **Step 1: Write the failing tests**

```python
# pi/tests/test_cam_store.py
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import cam_store


def test_init_db_creates_events_table(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'sub' / 'cam.db'))
    conn.execute('SELECT event_id, ts, diff_score FROM events')  # raises if missing
    conn.close()


def test_record_and_latest_event(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'cam.db'))
    cam_store.record_event(conn, 'evt1', 1000, 20.0)
    cam_store.record_event(conn, 'evt2', 2000, 30.0)
    assert cam_store.latest_event(conn) == {'event_id': 'evt2', 'ts': 2000}
    conn.close()


def test_latest_event_returns_none_when_empty(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'cam.db'))
    assert cam_store.latest_event(conn) is None
    conn.close()


def test_expired_events_returns_only_events_older_than_max_age(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'cam.db'))
    now = 1_000_000
    cam_store.record_event(conn, 'old', now - 8 * 86400, 15.0)
    cam_store.record_event(conn, 'recent', now - 1 * 86400, 15.0)
    assert cam_store.expired_events(conn, now, max_age_days=7) == ['old']
    conn.close()


def test_delete_event_removes_row(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'cam.db'))
    cam_store.record_event(conn, 'evt1', 1000, 20.0)
    cam_store.delete_event(conn, 'evt1')
    assert cam_store.latest_event(conn) is None
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pi/tests/test_cam_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cam_store'`

- [ ] **Step 3: Write the implementation**

```python
# pi/shared/cam_store.py
"""SQLite metadata store for camera motion events.

Deliberately holds no image bytes — the camera's own SD card is the photo
store (see the design spec). This just tracks which event_ids exist, when
they fired, and their diff score, so the Pi can answer "what are the recent
events" and "which events are old enough to prune" without asking the
camera every time.
"""
import os
import sqlite3

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS events (
  event_id   TEXT PRIMARY KEY,
  ts         INTEGER NOT NULL,
  diff_score REAL NOT NULL
);
'''


def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(_SCHEMA)
    return conn


def record_event(conn: sqlite3.Connection, event_id: str, ts: int, diff_score: float) -> None:
    conn.execute(
        'INSERT OR REPLACE INTO events (event_id, ts, diff_score) VALUES (?, ?, ?)',
        (event_id, ts, diff_score))
    conn.commit()


def expired_events(conn: sqlite3.Connection, now: int, max_age_days: int = 7) -> list[str]:
    cutoff = now - max_age_days * 86400
    rows = conn.execute('SELECT event_id FROM events WHERE ts < ?', (cutoff,)).fetchall()
    return [r[0] for r in rows]


def delete_event(conn: sqlite3.Connection, event_id: str) -> None:
    conn.execute('DELETE FROM events WHERE event_id = ?', (event_id,))
    conn.commit()


def latest_event(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        'SELECT event_id, ts FROM events ORDER BY ts DESC LIMIT 1').fetchone()
    return {'event_id': row[0], 'ts': row[1]} if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pi/tests/test_cam_store.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add pi/shared/cam_store.py pi/tests/test_cam_store.py
git commit -m "feat: add SQLite metadata store for camera motion events"
```

---

## Task 3: `cam_bridge.py` — frame intake, motion wiring, alert firing

**Files:**
- Create: `pi/scripts/cam_bridge.py`
- Test: `pi/tests/test_cam_bridge.py`

**Interfaces:**
- Consumes: `motion.downscale_grayscale`/`diff_score`/`is_motion` (Task 1), `cam_store.init_db`/`record_event` (Task 2), `push.send_push` (existing, `pi/shared/push.py`).
- Produces: Flask `app` object with route `POST /cam/frame`; module-level `_state_lock`, `_get_camera_ip()`, `_update_heartbeat(remote_addr)`, `_update_last_event(event_id)`, `_load_motion_alert_setting()`. Tasks 4-6 add to this same file and reuse these names exactly.

- [ ] **Step 1: Write the failing tests**

```python
# pi/tests/test_cam_bridge.py
import io
import json
import os
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import cam_bridge
import cam_store


def _jpeg(color: int) -> bytes:
    img = Image.new('L', (320, 240), color=color).convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return buf.getvalue()


def _fresh_client(tmp_path, monkeypatch):
    monkeypatch.setattr(cam_bridge, '_db_conn',
                         cam_store.init_db(str(tmp_path / 'cam.db')))
    monkeypatch.setattr(cam_bridge, '_prev_gray', None)
    monkeypatch.setattr(cam_bridge, '_camera_ip', None)
    monkeypatch.setattr(cam_bridge, '_last_seen', 0.0)
    monkeypatch.setattr(cam_bridge, '_last_event', None)
    monkeypatch.setattr(cam_bridge, 'send_push', lambda *a, **k: None)
    monkeypatch.setattr(cam_bridge, '_mqtt_client', None)
    return cam_bridge.app.test_client()


def test_cam_frame_discards_first_frame_no_prior_baseline(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    resp = client.post('/cam/frame', data=_jpeg(100), content_type='image/jpeg')
    assert resp.status_code == 200
    assert resp.data == b'discard'


def test_cam_frame_saves_on_large_change(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    client.post('/cam/frame', data=_jpeg(10), content_type='image/jpeg')
    resp = client.post('/cam/frame', data=_jpeg(250), content_type='image/jpeg')
    assert resp.data.startswith(b'save:')


def test_cam_frame_discards_on_small_change(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    client.post('/cam/frame', data=_jpeg(100), content_type='image/jpeg')
    resp = client.post('/cam/frame', data=_jpeg(101), content_type='image/jpeg')
    assert resp.data == b'discard'


def test_cam_frame_updates_heartbeat_and_camera_ip(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    client.post('/cam/frame', data=_jpeg(100), content_type='image/jpeg',
                 environ_overrides={'REMOTE_ADDR': '192.168.1.50'})
    assert cam_bridge._get_camera_ip() == '192.168.1.50'


def test_cam_frame_empty_body_discards_without_crashing(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    resp = client.post('/cam/frame', data=b'', content_type='image/jpeg')
    assert resp.data == b'discard'


def test_motion_fires_push_when_setting_enabled(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cam_bridge, 'send_push', lambda title, body: calls.append((title, body)))
    monkeypatch.setattr(cam_bridge, '_load_motion_alert_setting', lambda: True)
    client.post('/cam/frame', data=_jpeg(10), content_type='image/jpeg')
    client.post('/cam/frame', data=_jpeg(250), content_type='image/jpeg')
    assert len(calls) == 1
    assert calls[0][0] == 'Motion detected'


def test_motion_skips_push_when_setting_disabled(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cam_bridge, 'send_push', lambda title, body: calls.append((title, body)))
    monkeypatch.setattr(cam_bridge, '_load_motion_alert_setting', lambda: False)
    client.post('/cam/frame', data=_jpeg(10), content_type='image/jpeg')
    client.post('/cam/frame', data=_jpeg(250), content_type='image/jpeg')
    assert calls == []


def test_load_motion_alert_setting_defaults_true_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cam_bridge, 'NOTIFICATION_SETTINGS_CFG', str(tmp_path / 'missing.json'))
    assert cam_bridge._load_motion_alert_setting() is True


def test_load_motion_alert_setting_reads_false_from_file(tmp_path, monkeypatch):
    cfg = tmp_path / 'settings.json'
    cfg.write_text(json.dumps({'frost_forecast': True, 'daily_summary': True, 'motion_alert': False}))
    monkeypatch.setattr(cam_bridge, 'NOTIFICATION_SETTINGS_CFG', str(cfg))
    assert cam_bridge._load_motion_alert_setting() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pi/tests/test_cam_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cam_bridge'`

- [ ] **Step 3: Write the implementation**

```python
# pi/scripts/cam_bridge.py
#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — cam_bridge.py
# Receives periodic snapshots from the ESP32-CAM, runs grayscale frame-diff
# motion detection, tells the camera which frames to keep on its own SD card,
# fires push alerts, and relays event photos / on-demand live frames to the
# app over MQTT. See docs/superpowers/specs/2026-07-10-esp32-cam-integration-design.md.
# ═══════════════════════════════════════════════════════════════════════════
import json
import os
import sys
import threading
import time

from flask import Flask, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import cam_store
import motion
from push import send_push

# ── Config ───────────────────────────────────────────────────────────────────
CAM_HTTP_PORT = 8090
DB_PATH = '/var/lib/greenhouse/cam_events.db'
NOTIFICATION_SETTINGS_CFG = '/etc/greenhouse/notification_settings.json'
MOTION_THRESHOLD = 12.0

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
    global _db_conn
    _db_conn = cam_store.init_db(DB_PATH)
    print(f'[cam_bridge] Starting HTTP server on port {CAM_HTTP_PORT}', flush=True)
    app.run(host='0.0.0.0', port=CAM_HTTP_PORT, debug=False)


if __name__ == '__main__':
    run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pi/tests/test_cam_bridge.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/cam_bridge.py pi/tests/test_cam_bridge.py
git commit -m "feat: add cam_bridge frame intake with motion detection and push alerts"
```

---

## Task 4: `cam_bridge.py` — MQTT client, status heartbeat, event-photo relay

**Files:**
- Modify: `pi/scripts/cam_bridge.py`
- Modify: `pi/tests/test_cam_bridge.py`

**Interfaces:**
- Consumes: `_get_camera_ip()`, `_state_lock`, `_camera_ip`, `_last_seen`, `_last_event` from Task 3.
- Produces: `cam_bridge._publish_chunked(client, topic, data, extra=None)`, `cam_bridge.publish_status(client)`, `cam_bridge._handle_event_request(client, raw_payload)`, `cam_bridge.on_connect`, `cam_bridge.on_message`, constants `STATUS_TOPIC`, `EVENT_REQUEST_TOPIC`, `EVENT_RESPONSE_PREFIX`, `CHUNK_SIZE`, `HEARTBEAT_STALE_SECONDS`. Task 5 imports `_publish_chunked` for live frames; Task 6 imports `publish_status`.

- [ ] **Step 1: Write the failing tests (append to `pi/tests/test_cam_bridge.py`)**

```python
# --- append to pi/tests/test_cam_bridge.py ---
import base64
from unittest.mock import MagicMock


def test_publish_chunked_splits_data_and_encodes_base64(monkeypatch):
    client = MagicMock()
    data = b'x' * 7000  # > 2 chunks at CHUNK_SIZE=3072
    cam_bridge._publish_chunked(client, 'some/topic', data)
    assert client.publish.call_count == 3
    payloads = [json.loads(c.args[1]) for c in client.publish.call_args_list]
    assert [p['chunk'] for p in payloads] == [0, 1, 2]
    assert all(p['total'] == 3 for p in payloads)
    reassembled = b''.join(base64.b64decode(p['data']) for p in payloads)
    assert reassembled == data


def test_publish_chunked_single_chunk_for_small_data(monkeypatch):
    client = MagicMock()
    cam_bridge._publish_chunked(client, 'some/topic', b'small')
    assert client.publish.call_count == 1
    payload = json.loads(client.publish.call_args.args[1])
    assert payload == {'chunk': 0, 'total': 1, 'data': base64.b64encode(b'small').decode('ascii')}


def test_publish_chunked_includes_extra_fields(monkeypatch):
    client = MagicMock()
    cam_bridge._publish_chunked(client, 'some/topic', b'x', extra={'frame_id': 5})
    payload = json.loads(client.publish.call_args.args[1])
    assert payload['frame_id'] == 5


def test_publish_status_reports_online_within_heartbeat_window(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', '192.168.1.50')
    monkeypatch.setattr(cam_bridge, '_last_seen', time.time())
    monkeypatch.setattr(cam_bridge, '_last_event', {'event_id': 'evt1', 'ts': 1000})
    client = MagicMock()
    cam_bridge.publish_status(client)
    payload = json.loads(client.publish.call_args.args[1])
    assert payload['online'] is True
    assert payload['ip'] == '192.168.1.50'
    assert payload['last_event'] == {'event_id': 'evt1', 'ts': 1000}


def test_publish_status_reports_offline_when_stale(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', '192.168.1.50')
    monkeypatch.setattr(cam_bridge, '_last_seen', time.time() - 999)
    client = MagicMock()
    cam_bridge.publish_status(client)
    payload = json.loads(client.publish.call_args.args[1])
    assert payload['online'] is False


def test_handle_event_request_relays_photo_from_camera(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', '192.168.1.50')
    monkeypatch.setattr(cam_bridge, 'urlopen', lambda req, timeout=8: io.BytesIO(b'jpegbytes'))
    client = MagicMock()
    cam_bridge._handle_event_request(client, json.dumps({'id': 'req1', 'event_id': 'evt1'}).encode())
    topic, payload = client.publish.call_args.args[:2]
    assert topic == 'greenhouse/cam/event/response/req1'
    assert json.loads(payload)['data'] == base64.b64encode(b'jpegbytes').decode('ascii')


def test_handle_event_request_reports_error_when_camera_unreachable(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', None)
    client = MagicMock()
    cam_bridge._handle_event_request(client, json.dumps({'id': 'req1', 'event_id': 'evt1'}).encode())
    topic, payload = client.publish.call_args.args[:2]
    assert topic == 'greenhouse/cam/event/response/req1'
    assert json.loads(payload) == {'error': 'camera_unreachable'}


def test_handle_event_request_ignores_malformed_payload(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    client = MagicMock()
    cam_bridge._handle_event_request(client, b'not json')
    client.publish.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pi/tests/test_cam_bridge.py -v`
Expected: FAIL — `AttributeError: module 'cam_bridge' has no attribute '_publish_chunked'` (and similar for the other new names)

- [ ] **Step 3: Write the implementation (add to `pi/scripts/cam_bridge.py`)**

Add near the top, alongside the other imports/config:

```python
import base64
import urllib.error
import urllib.request
from urllib.request import urlopen

import paho.mqtt.client as mqtt

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
```

Add these functions (after `_fire_motion_alert`, before the Flask routes):

```python
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
```

Replace `run()` with:

```python
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
```

Note: the test file monkeypatches `cam_bridge.urlopen` directly (imported via `from urllib.request import urlopen` above), so `_handle_event_request` must call the module-level `urlopen(...)` name (not `urllib.request.urlopen(...)`) for the test's monkeypatch to take effect — this is already how the code above is written.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pi/tests/test_cam_bridge.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/cam_bridge.py pi/tests/test_cam_bridge.py
git commit -m "feat: add cam_bridge MQTT status heartbeat and event-photo relay"
```

---

## Task 5: `cam_bridge.py` — on-demand live relay + `motion_alert` setting round-trip

**Files:**
- Modify: `pi/scripts/cam_bridge.py`
- Modify: `pi/tests/test_cam_bridge.py`
- Modify: `pi/scripts/weather.py:121-153` (carry `motion_alert` through the shared notification-settings file)
- Modify: `pi/tests/test_weather_rules.py` (cover the extended settings dict)

**Interfaces:**
- Consumes: `_publish_chunked` (Task 4), `_get_camera_ip()` (Task 3).
- Produces: `cam_bridge._start_live_session(client)`, `cam_bridge._stop_live_session()`, `cam_bridge._live_loop(client, stop_event)`, constants `LIVE_START_TOPIC`, `LIVE_STOP_TOPIC`, `LIVE_FRAME_TOPIC`, `LIVE_POLL_INTERVAL`, `LIVE_SESSION_TIMEOUT`.

- [ ] **Step 1: Write the failing tests**

First, extend `weather.py`'s settings shape — append to `pi/tests/test_weather_rules.py`:

```python
# --- append to pi/tests/test_weather_rules.py ---
def test_load_notification_settings_includes_motion_alert_default_true(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, 'NOTIFICATION_SETTINGS_CFG', str(tmp_path / 'missing.json'))
    settings = weather.load_notification_settings()
    assert settings['motion_alert'] is True


def test_pull_notification_settings_preserves_motion_alert(tmp_path, monkeypatch):
    cfg_path = tmp_path / 'settings.json'
    monkeypatch.setattr(weather, 'NOTIFICATION_SETTINGS_CFG', str(cfg_path))
    fake_result = MagicMock()
    fake_result.stdout = json.dumps({
        'frost_forecast': True, 'daily_summary': True, 'motion_alert': False,
    })
    monkeypatch.setattr(weather.subprocess, 'run', lambda *a, **k: fake_result)
    weather._pull_notification_settings()
    saved = json.loads(cfg_path.read_text())
    assert saved['motion_alert'] is False
```

(This test file already imports `weather` and `json`/`MagicMock` per the existing file — no new imports needed.)

Now add live-relay tests, appended to `pi/tests/test_cam_bridge.py`:

```python
# --- append to pi/tests/test_cam_bridge.py ---
import threading


def test_start_live_session_spawns_a_thread(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', None)  # no camera to poll — loop just idles
    monkeypatch.setattr(cam_bridge, 'LIVE_POLL_INTERVAL', 0.01)
    client = MagicMock()
    cam_bridge._start_live_session(client)
    assert cam_bridge._live_thread is not None
    assert cam_bridge._live_thread.is_alive()
    cam_bridge._stop_live_session()
    cam_bridge._live_thread.join(timeout=2)
    assert not cam_bridge._live_thread.is_alive()


def test_start_live_session_is_idempotent_while_already_running(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', None)
    monkeypatch.setattr(cam_bridge, 'LIVE_POLL_INTERVAL', 0.01)
    client = MagicMock()
    cam_bridge._start_live_session(client)
    first_thread = cam_bridge._live_thread
    cam_bridge._start_live_session(client)  # treated as a keep-alive, not a second session
    assert cam_bridge._live_thread is first_thread
    cam_bridge._stop_live_session()
    first_thread.join(timeout=2)


def test_live_loop_publishes_frames_while_camera_reachable(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', '192.168.1.50')
    monkeypatch.setattr(cam_bridge, 'LIVE_POLL_INTERVAL', 0.01)
    monkeypatch.setattr(cam_bridge, 'urlopen', lambda req, timeout=5: io.BytesIO(b'frame'))
    client = MagicMock()
    stop_event = threading.Event()

    def _stop_soon():
        time.sleep(0.05)
        stop_event.set()
    threading.Thread(target=_stop_soon).start()

    cam_bridge._live_loop(client, stop_event)

    assert client.publish.call_count >= 1
    topic, payload = client.publish.call_args_list[0].args[:2]
    assert topic == cam_bridge.LIVE_FRAME_TOPIC
    assert 'frame_id' in json.loads(payload)


def test_live_loop_stops_after_timeout_without_keepalive(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', None)
    monkeypatch.setattr(cam_bridge, 'LIVE_POLL_INTERVAL', 0.01)
    monkeypatch.setattr(cam_bridge, 'LIVE_SESSION_TIMEOUT', 0.05)
    client = MagicMock()
    cam_bridge._live_last_start = time.monotonic() - 1  # already stale
    stop_event = threading.Event()

    start = time.monotonic()
    cam_bridge._live_loop(client, stop_event)
    assert time.monotonic() - start < 2  # returned promptly instead of looping forever


def test_on_message_routes_live_start_and_stop(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', None)
    monkeypatch.setattr(cam_bridge, 'LIVE_POLL_INTERVAL', 0.01)
    client = MagicMock()
    msg = MagicMock(topic=cam_bridge.LIVE_START_TOPIC, payload=b'1')
    cam_bridge.on_message(client, None, msg)
    assert cam_bridge._live_thread is not None
    stop_msg = MagicMock(topic=cam_bridge.LIVE_STOP_TOPIC, payload=b'1')
    cam_bridge.on_message(client, None, stop_msg)
    cam_bridge._live_thread.join(timeout=2)
    assert not cam_bridge._live_thread.is_alive()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pi/tests/test_cam_bridge.py pi/tests/test_weather_rules.py -v`
Expected: FAIL — `AttributeError` for `_start_live_session`/`_stop_live_session`/`_live_loop`/`_live_thread`/`LIVE_*`, and `KeyError: 'motion_alert'` for the weather.py tests.

- [ ] **Step 3: Write the implementation**

In `pi/scripts/weather.py`, extend `_pull_notification_settings()` and `load_notification_settings()` (`pi/scripts/weather.py:121-153`) so `motion_alert` survives the round trip — cam_bridge.py reads this same file:

```python
def _pull_notification_settings():
    """Check for a retained settings/notifications message published by the app."""
    import subprocess
    try:
        result = subprocess.run(
            ['mosquitto_sub', '-h', MQTT_HOST, '-p', MQTT_PORT,
             '-t', 'greenhouse/settings/notifications', '-C', '1', '-W', '2'],
            capture_output=True, text=True, timeout=5,
        )
        msg = result.stdout.strip()
        if not msg:
            return
        data = json.loads(msg)
        settings = {
            'frost_forecast': bool(data.get('frost_forecast', True)),
            'daily_summary':  bool(data.get('daily_summary', True)),
            'motion_alert':   bool(data.get('motion_alert', True)),
        }
        with open(NOTIFICATION_SETTINGS_CFG, 'w') as f:
            json.dump(settings, f)
    except Exception as e:
        print(f'[weather] WARN: notification settings pull: {e}', flush=True)


def load_notification_settings() -> dict:
    try:
        with open(NOTIFICATION_SETTINGS_CFG) as f:
            d = json.load(f)
        return {
            'frost_forecast': bool(d.get('frost_forecast', True)),
            'daily_summary':  bool(d.get('daily_summary', True)),
            'motion_alert':   bool(d.get('motion_alert', True)),
        }
    except Exception:
        return {'frost_forecast': True, 'daily_summary': True, 'motion_alert': True}
```

In `pi/scripts/cam_bridge.py`, add near the other topic constants:

```python
LIVE_START_TOPIC = 'greenhouse/cam/live/start'
LIVE_STOP_TOPIC = 'greenhouse/cam/live/stop'
LIVE_FRAME_TOPIC = 'greenhouse/cam/live/frame'
LIVE_POLL_INTERVAL = 0.7   # seconds between camera /capture polls (~1.4fps)
LIVE_SESSION_TIMEOUT = 120  # seconds without a keep-alive before auto-stopping
```

Add live-session state and functions (after `_handle_event_request`):

```python
_live_thread: threading.Thread | None = None
_live_stop_event = threading.Event()
_live_last_start = 0.0


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
                with urlopen(urllib.request.Request(f'http://{camera_ip}/capture'), timeout=5) as resp:
                    jpeg_bytes = resp.read()
                _publish_chunked(client, LIVE_FRAME_TOPIC, jpeg_bytes, extra={'frame_id': frame_id})
                frame_id += 1
            except Exception as e:
                print(f'[cam_bridge] WARN: live frame fetch failed: {e}', flush=True)
        stop_event.wait(LIVE_POLL_INTERVAL)
    print('[cam_bridge] Live session ended', flush=True)
```

Update `on_connect`/`on_message` to also subscribe to and route the live topics:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pi/tests/test_cam_bridge.py pi/tests/test_weather_rules.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/cam_bridge.py pi/tests/test_cam_bridge.py pi/scripts/weather.py pi/tests/test_weather_rules.py
git commit -m "feat: add cam_bridge on-demand live relay and motion_alert setting"
```

---

## Task 6: `cam_bridge.py` — maintenance thread (pruning + offline detection)

**Files:**
- Modify: `pi/scripts/cam_bridge.py`
- Modify: `pi/tests/test_cam_bridge.py`

**Interfaces:**
- Consumes: `cam_store.expired_events`/`delete_event` (Task 2), `publish_status` (Task 4), `_get_camera_ip()` (Task 3).
- Produces: `cam_bridge._prune_expired_events(conn)`, `cam_bridge.maintenance_loop(conn, client, interval_seconds=60)`. `run()` (final version) starts this as a daemon thread.

- [ ] **Step 1: Write the failing tests (append to `pi/tests/test_cam_bridge.py`)**

```python
# --- append to pi/tests/test_cam_bridge.py ---
def test_prune_expired_events_deletes_on_camera_then_from_db(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', '192.168.1.50')
    cam_store.record_event(cam_bridge._db_conn, 'old', 1, 20.0)
    deleted_urls = []
    monkeypatch.setattr(cam_bridge, 'urlopen', lambda req, timeout=5: deleted_urls.append(req.full_url))
    cam_bridge._prune_expired_events(cam_bridge._db_conn)
    assert deleted_urls == ['http://192.168.1.50/event/old']
    assert cam_store.latest_event(cam_bridge._db_conn) is None


def test_prune_expired_events_keeps_metadata_when_camera_unreachable(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', None)
    cam_store.record_event(cam_bridge._db_conn, 'old', 1, 20.0)
    cam_bridge._prune_expired_events(cam_bridge._db_conn)
    assert cam_store.latest_event(cam_bridge._db_conn) == {'event_id': 'old', 'ts': 1}


def test_prune_expired_events_keeps_metadata_when_delete_request_fails(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', '192.168.1.50')
    cam_store.record_event(cam_bridge._db_conn, 'old', 1, 20.0)

    def _raise(*a, **k):
        raise OSError('unreachable')
    monkeypatch.setattr(cam_bridge, 'urlopen', _raise)

    cam_bridge._prune_expired_events(cam_bridge._db_conn)
    assert cam_store.latest_event(cam_bridge._db_conn) == {'event_id': 'old', 'ts': 1}


def test_maintenance_loop_runs_prune_and_publish_once_per_tick(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    calls = {'prune': 0, 'status': 0}
    monkeypatch.setattr(cam_bridge, '_prune_expired_events', lambda conn: calls.__setitem__('prune', calls['prune'] + 1))
    monkeypatch.setattr(cam_bridge, 'publish_status', lambda client: calls.__setitem__('status', calls['status'] + 1))
    client = MagicMock()
    stop_after_one = threading.Event()

    def _run_once():
        cam_bridge.maintenance_loop(cam_bridge._db_conn, client, interval_seconds=0.01, _stop_event=stop_after_one)
    t = threading.Thread(target=_run_once)
    t.start()
    time.sleep(0.05)
    stop_after_one.set()
    t.join(timeout=2)
    assert calls['prune'] >= 1
    assert calls['status'] >= 1


def test_maintenance_loop_survives_prune_exception(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_prune_expired_events', lambda conn: (_ for _ in ()).throw(Exception('boom')))
    status_calls = []
    monkeypatch.setattr(cam_bridge, 'publish_status', lambda client: status_calls.append(1))
    client = MagicMock()
    stop_event = threading.Event()

    def _run_once():
        cam_bridge.maintenance_loop(cam_bridge._db_conn, client, interval_seconds=0.01, _stop_event=stop_event)
    t = threading.Thread(target=_run_once)
    t.start()
    time.sleep(0.05)
    stop_event.set()
    t.join(timeout=2)
    assert len(status_calls) >= 1  # status still publishes despite the pruning exception
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pi/tests/test_cam_bridge.py -v`
Expected: FAIL — `AttributeError: module 'cam_bridge' has no attribute '_prune_expired_events'` (and `maintenance_loop`)

- [ ] **Step 3: Write the implementation**

Add near the other config constants:

```python
EVENT_MAX_AGE_DAYS = 7
```

Add these functions (after the live-relay code):

```python
def _prune_expired_events(conn) -> None:
    now = int(time.time())
    for event_id in cam_store.expired_events(conn, now, EVENT_MAX_AGE_DAYS):
        camera_ip = _get_camera_ip()
        if camera_ip is None:
            continue  # retry next cycle once the camera is reachable again
        try:
            urlopen(urllib.request.Request(f'http://{camera_ip}/event/{event_id}', method='DELETE'),
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
```

Replace `run()`'s body to start the maintenance thread:

```python
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
```

Note: `maintenance_loop` uses `stop_event.wait(interval_seconds)` (returns `True` early if the event is set, `False` on a normal timeout) rather than a plain `time.sleep`, purely so tests can inject `_stop_event` and end the loop deterministically instead of asserting on a background thread that never stops.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pi/tests/test_cam_bridge.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/cam_bridge.py pi/tests/test_cam_bridge.py
git commit -m "feat: add cam_bridge maintenance thread for pruning and status publish"
```

---

## Task 7: systemd unit + install.sh wiring

**Files:**
- Create: `pi/systemd/greenhouse-cam-bridge.service`
- Modify: `pi/install.sh:20-27` (apt package list), `pi/install.sh:123-138` (systemd install/enable block)

**Interfaces:**
- Consumes: `pi/scripts/cam_bridge.py` (Task 6, final version).
- Produces: an enabled, auto-restarting systemd service, same operational shape as `greenhouse-weather.service`.

- [ ] **Step 1: Create the systemd unit**

```ini
# pi/systemd/greenhouse-cam-bridge.service
[Unit]
Description=Greenhouse Camera Bridge (motion detection + live relay)
After=network-online.target mosquitto.service
Wants=network-online.target
Requires=mosquitto.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/pi/greenhouse/scripts/cam_bridge.py
Restart=always
RestartSec=10
User=pi
WorkingDirectory=/home/pi/greenhouse

TimeoutStopSec=15
KillSignal=SIGTERM

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/tmp /etc/greenhouse /var/lib/greenhouse

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Add the Pillow apt package to `pi/install.sh`**

In the `apt-get install` block (`pi/install.sh:20-27`), add `python3-pil` — Debian's precompiled Pillow package, chosen specifically to avoid the pip/piwheels source-build trap that crashed this same Pi Zero W twice during the FCM push-notification bench test (see `pi/install.sh`'s existing `firebase-admin` comment and project memory: prefer an apt-provided wheel over pip wherever one exists, on this board):

```bash
apt-get install -y -qq \
  mosquitto mosquitto-clients \
  python3-flask \
  python3-paho-mqtt \
  python3-pil \
  python3-pip \
  openssl \
  dnsmasq-base \
  iptables \
  rfkill \
  avahi-daemon
```

- [ ] **Step 3: Install and enable the service (`pi/install.sh:123-138`)**

```bash
echo "==> Installing systemd services..."
cp "$REPO"/systemd/greenhouse-firstboot.service      /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-portal.service         /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-ap.service             /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-wifi-watchdog.service  /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-weather.service        /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-recorder.service       /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-hivemq-bridge.service  /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-cam-bridge.service     /etc/systemd/system/

# Ensure Mosquitto starts AFTER first_boot has generated certs on a fresh unit.
mkdir -p /etc/systemd/system/mosquitto.service.d
cat > /etc/systemd/system/mosquitto.service.d/greenhouse.conf <<EOF
[Unit]
After=greenhouse-firstboot.service
EOF

systemctl daemon-reload
systemctl enable greenhouse-firstboot greenhouse-portal greenhouse-ap greenhouse-wifi-watchdog greenhouse-weather greenhouse-recorder greenhouse-hivemq-bridge greenhouse-cam-bridge >/dev/null 2>&1
```

- [ ] **Step 4: Verify the changed files are syntactically sane**

Run: `bash -n pi/install.sh`
Expected: no output (bash's `-n` flag parses without executing; a non-zero exit or printed error means a syntax mistake was introduced)

- [ ] **Step 5: Commit**

```bash
git add pi/systemd/greenhouse-cam-bridge.service pi/install.sh
git commit -m "feat: wire cam_bridge into install.sh and systemd"
```

---

## Task 8: ESP32-CAM firmware (`firmware/cam_esp32/cam_esp32.ino`)

**Files:**
- Create: `firmware/cam_esp32/cam_esp32.ino`

**Interfaces:**
- Consumes: `pi/scripts/cam_bridge.py`'s `/cam/frame` contract (raw JPEG POST body, plain-text `discard` or `save:<event_id>` response) and event-file API contract (`GET`/`DELETE /event/<event_id>`).
- Produces: `/stream` (MJPEG), `/capture` (single JPEG), `/event/<id>` (GET serves, DELETE removes) on the camera's own web server, consumed by `cam_bridge.py`'s `_handle_event_request`/`_live_loop`/`_prune_expired_events` (Tasks 4-6).

This is firmware for hardware already in hand but not yet flashed — same situation as the existing mesh-relay sketches. It cannot be compiled or bench-tested in this environment. **The test cycle for this task is the manual checklist in Step 2, not automated tests.**

- [ ] **Step 1: Write the complete sketch**

```cpp
// firmware/cam_esp32/cam_esp32.ino
// ═══════════════════════════════════════════════════════════════════════════
// Greenhouse IoT — ESP32-CAM (AI-Thinker)
// Serves a LAN MJPEG stream + single-frame capture (for the Pi's live relay
// and the app's direct LAN view), POSTs periodic snapshots to the Pi for
// motion detection, and stores Pi-flagged motion-event frames on its own SD
// card (served/deleted via a tiny HTTP API the Pi calls on demand).
// See docs/superpowers/specs/2026-07-10-esp32-cam-integration-design.md.
// ═══════════════════════════════════════════════════════════════════════════
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <esp_camera.h>
#include <SD_MMC.h>
#include <FS.h>

// ── WiFi (home router) ────────────────────────────────────────────────────────
#define WIFI_SSID     "TP-Link_14A6"
#define WIFI_PASSWORD "6940604664"

// ── Pi cam_bridge endpoint ────────────────────────────────────────────────────
#define PI_HOST "greenhouse.local"
#define PI_PORT 8090

// ── AI-Thinker ESP32-CAM pin map (standard, from Espressif's camera examples) ─
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

WebServer server(80);

const uint32_t SNAPSHOT_INTERVAL_MS = 3000;
uint32_t lastSnapshotMs = 0;

// ── Camera init ────────────────────────────────────────────────────────────────
bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;   // 640x480 — used for /stream and /capture
  config.jpeg_quality = 12;
  config.fb_count = 2;

  return esp_camera_init(&config) == ESP_OK;
}

// ── /capture: single JPEG frame ────────────────────────────────────────────────
void handleCapture() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) { server.send(503, "text/plain", "capture failed"); return; }
  server.sendHeader("Content-Type", "image/jpeg");
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  server.client().write(fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

// ── /stream: continuous MJPEG (multipart/x-mixed-replace) ─────────────────────
void handleStream() {
  WiFiClient client = server.client();
  String boundary = "greenhousecamframe";
  client.printf(
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: multipart/x-mixed-replace; boundary=%s\r\n\r\n",
    boundary.c_str());
  while (client.connected()) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) break;
    client.printf("--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                  boundary.c_str(), fb->len);
    client.write(fb->buf, fb->len);
    client.print("\r\n");
    esp_camera_fb_return(fb);
    if (!client.connected()) break;
    delay(50);  // ~20fps cap, matches the design's "genuinely smooth LAN view"
  }
}

// ── /event/<id>: serve or delete a saved motion-event JPEG ─────────────────────
String eventPath(const String &eventId) {
  // Sanitize to the same charset cam_bridge.py generates ("evt" + digits) —
  // reject anything else rather than trusting a path segment as a filename.
  for (size_t i = 0; i < eventId.length(); i++) {
    char c = eventId[i];
    if (!isalnum(c)) return "";
  }
  return "/" + eventId + ".jpg";
}

void handleEventGet() {
  String eventId = server.pathArg(0);
  String path = eventPath(eventId);
  if (path == "" || !SD_MMC.exists(path)) {
    server.send(404, "text/plain", "not found");
    return;
  }
  File f = SD_MMC.open(path, FILE_READ);
  server.streamFile(f, "image/jpeg");
  f.close();
}

void handleEventDelete() {
  String eventId = server.pathArg(0);
  String path = eventPath(eventId);
  if (path == "" || !SD_MMC.exists(path)) {
    server.send(404, "text/plain", "not found");
    return;
  }
  SD_MMC.remove(path);
  server.send(200, "text/plain", "deleted");
}

// ── Periodic snapshot POST to the Pi (motion-detection intake) ────────────────
void sendSnapshotToPi() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return;

  HTTPClient http;
  String url = String("http://") + PI_HOST + ":" + PI_PORT + "/cam/frame";
  http.begin(url);
  http.addHeader("Content-Type", "image/jpeg");
  int code = http.POST(fb->buf, fb->len);

  if (code == 200) {
    String resp = http.getString();
    if (resp.startsWith("save:")) {
      String eventId = resp.substring(5);
      String path = eventPath(eventId);
      if (path != "") {
        File f = SD_MMC.open(path, FILE_WRITE);
        if (f) {
          f.write(fb->buf, fb->len);
          f.close();
          Serial.printf("[cam] Saved motion event %s (%u bytes)\n", eventId.c_str(), fb->len);
        } else {
          Serial.printf("[cam] WARN: could not open %s for writing\n", path.c_str());
        }
      }
    }
  } else {
    Serial.printf("[cam] WARN: snapshot POST failed, code=%d\n", code);
  }
  http.end();
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);

  if (!initCamera()) {
    Serial.println("[cam] FATAL: camera init failed");
    return;
  }

  if (!SD_MMC.begin("/sdcard", true)) {  // true = 1-bit mode (AI-Thinker shares
                                          // camera pins with 4-bit SD mode)
    Serial.println("[cam] WARN: SD card init failed — motion events won't persist");
  }

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[cam] Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[cam] Connected, IP=%s\n", WiFi.localIP().toString().c_str());

  server.on("/capture", HTTP_GET, handleCapture);
  server.on("/stream", HTTP_GET, handleStream);
  server.on(UriBraces("/event/{}"), HTTP_GET, handleEventGet);
  server.on(UriBraces("/event/{}"), HTTP_DELETE, handleEventDelete);
  server.begin();
  Serial.println("[cam] HTTP server started");
}

void loop() {
  server.handleClient();

  uint32_t now = millis();
  if (now - lastSnapshotMs >= SNAPSHOT_INTERVAL_MS) {
    lastSnapshotMs = now;
    sendSnapshotToPi();
  }
}
```

- [ ] **Step 2: Manual bench-validation checklist (once flashed)**

This replaces the usual "run tests" step — there is no compiler or hardware in this environment. Once you flash this sketch to the physical ESP32-CAM:

1. Confirm `http://<camera-ip>/stream` loads directly in a browser on the home LAN and shows a smooth live feed.
2. Confirm `http://<camera-ip>/capture` returns a single JPEG.
3. Start `greenhouse-cam-bridge` on the Pi (`sudo systemctl status greenhouse-cam-bridge`) and confirm periodic snapshot POSTs are arriving (`journalctl -u greenhouse-cam-bridge -f` should show no repeated WARNs).
4. Wave a hand in front of the camera; confirm a push notification arrives on the phone within a few seconds.
5. Tap the notification (or open the Camera screen's events list) and confirm the event photo loads.
6. Repeat step 5 with the phone on mobile data, WiFi off, to exercise the MQTT relay path instead of LAN HTTP.
7. Open the Camera screen's live view on LAN — confirm it's smooth. Switch the phone to mobile data and reopen — confirm the low-fps MQTT-relayed view appears instead, with the "refreshing ~1x/sec" indicator.
8. Unplug the camera; confirm the dashboard status card flips to offline within `HEARTBEAT_STALE_SECONDS` (9s) plus one maintenance-loop tick (up to 60s).

- [ ] **Step 3: Commit**

```bash
git add firmware/cam_esp32/cam_esp32.ino
git commit -m "feat: add ESP32-CAM firmware (stream, capture, motion-event SD store)"
```

---

## Task 9: App models — `CamStatus`, `CamEvent`, extended `NotificationSettings`/`WeatherAlert`

**Files:**
- Create: `app/lib/models/cam_status.dart`
- Create: `app/lib/models/cam_event.dart`
- Modify: `app/lib/models/notification_settings.dart`
- Modify: `app/lib/models/weather_alert.dart`
- Test: `app/test/models/cam_status_test.dart`
- Test: `app/test/models/cam_event_test.dart`
- Modify: `app/test/models/weather_rule_test.dart`'s sibling — actually add: `app/test/models/notification_settings_test.dart`, `app/test/models/weather_alert_test.dart`

**Interfaces:**
- Produces: `CamStatus.fromJson`, `CamStatus.online/lastSeen/ip/lastEvent`; `CamEvent(eventId, ts)`; `NotificationSettings.motionAlert` (new field); `WeatherAlert.title` handling `type == 'motion'`. Tasks 10-12 consume these.

- [ ] **Step 1: Write the failing tests**

```dart
// app/test/models/cam_status_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/cam_status.dart';

void main() {
  test('fromJson parses online status with last event', () {
    final status = CamStatus.fromJson({
      'online': true,
      'last_seen': 1700000000.0,
      'ip': '192.168.1.50',
      'last_event': {'event_id': 'evt1', 'ts': 1700000000},
    });
    expect(status.online, isTrue);
    expect(status.ip, '192.168.1.50');
    expect(status.lastEvent?.eventId, 'evt1');
  });

  test('fromJson handles a null last_event (no motion yet)', () {
    final status = CamStatus.fromJson({
      'online': false, 'last_seen': null, 'ip': null, 'last_event': null,
    });
    expect(status.online, isFalse);
    expect(status.lastEvent, isNull);
  });
}
```

```dart
// app/test/models/cam_event_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/cam_event.dart';

void main() {
  test('fromJson parses event_id and ts', () {
    final event = CamEvent.fromJson({'event_id': 'evt1', 'ts': 1700000000});
    expect(event.eventId, 'evt1');
    expect(event.ts, 1700000000);
  });

  test('timestamp converts unix seconds to a DateTime', () {
    final event = CamEvent.fromJson({'event_id': 'evt1', 'ts': 1700000000});
    expect(event.timestamp, DateTime.fromMillisecondsSinceEpoch(1700000000 * 1000));
  });
}
```

```dart
// app/test/models/notification_settings_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/notification_settings.dart';

void main() {
  test('fromJson defaults motionAlert to true when absent', () {
    final settings = NotificationSettings.fromJson({'frost_forecast': true, 'daily_summary': true});
    expect(settings.motionAlert, isTrue);
  });

  test('toJson includes motion_alert', () {
    const settings = NotificationSettings(frostForecast: true, dailySummary: true, motionAlert: false);
    expect(settings.toJson(), {'frost_forecast': true, 'daily_summary': true, 'motion_alert': false});
  });

  test('copyWith updates motionAlert independently', () {
    const settings = NotificationSettings(frostForecast: true, dailySummary: true, motionAlert: true);
    final updated = settings.copyWith(motionAlert: false);
    expect(updated.motionAlert, isFalse);
    expect(updated.frostForecast, isTrue);
  });
}
```

```dart
// app/test/models/weather_alert_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/weather_alert.dart';

void main() {
  test('title for a motion alert', () {
    final alert = WeatherAlert.fromJson({'type': 'motion', 'message': 'Motion detected', 'severity': 'info'});
    expect(alert.title, contains('Motion'));
  });
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `flutter test app/test/models/cam_status_test.dart app/test/models/cam_event_test.dart app/test/models/notification_settings_test.dart app/test/models/weather_alert_test.dart`
Expected: FAIL — `cam_status.dart`/`cam_event.dart` don't exist; the `NotificationSettings`/`WeatherAlert` tests fail on missing `motionAlert` field / wrong title text.

- [ ] **Step 3: Write the implementation**

```dart
// app/lib/models/cam_event.dart
class CamEvent {
  final String eventId;
  final int ts; // unix seconds

  const CamEvent({required this.eventId, required this.ts});

  factory CamEvent.fromJson(Map<String, dynamic> json) => CamEvent(
        eventId: json['event_id'] as String,
        ts: (json['ts'] as num).toInt(),
      );

  DateTime get timestamp => DateTime.fromMillisecondsSinceEpoch(ts * 1000);
}
```

```dart
// app/lib/models/cam_status.dart
import 'package:greenhouse_app/models/cam_event.dart';

class CamStatus {
  final bool online;
  final double? lastSeen;
  final String? ip;
  final CamEvent? lastEvent;

  const CamStatus({required this.online, this.lastSeen, this.ip, this.lastEvent});

  factory CamStatus.fromJson(Map<String, dynamic> json) => CamStatus(
        online: json['online'] as bool? ?? false,
        lastSeen: (json['last_seen'] as num?)?.toDouble(),
        ip: json['ip'] as String?,
        lastEvent: json['last_event'] != null
            ? CamEvent.fromJson(json['last_event'] as Map<String, dynamic>)
            : null,
      );
}
```

Replace `app/lib/models/notification_settings.dart` entirely:

```dart
// app/lib/models/notification_settings.dart
class NotificationSettings {
  final bool frostForecast;
  final bool dailySummary;
  final bool motionAlert;

  const NotificationSettings({
    required this.frostForecast,
    required this.dailySummary,
    this.motionAlert = true,
  });

  factory NotificationSettings.fromJson(Map<String, dynamic> json) => NotificationSettings(
        frostForecast: json['frost_forecast'] as bool? ?? true,
        dailySummary: json['daily_summary'] as bool? ?? true,
        motionAlert: json['motion_alert'] as bool? ?? true,
      );

  Map<String, dynamic> toJson() => {
        'frost_forecast': frostForecast,
        'daily_summary': dailySummary,
        'motion_alert': motionAlert,
      };

  NotificationSettings copyWith({bool? frostForecast, bool? dailySummary, bool? motionAlert}) =>
      NotificationSettings(
        frostForecast: frostForecast ?? this.frostForecast,
        dailySummary: dailySummary ?? this.dailySummary,
        motionAlert: motionAlert ?? this.motionAlert,
      );
}
```

In `app/lib/models/weather_alert.dart`, add a `motion` case to the `title` switch:

```dart
  String get title {
    switch (type) {
      case 'frost':          return '❄️ Frost Alert';
      case 'daily_summary':  return '🌤 Daily Forecast';
      case 'rain-close':     return '🌧 Rain Alert';
      case 'frost-heat':     return '❄️ Frost Protection';
      case 'heat-fan':       return '🌡 Heat Wave';
      case 'motion':         return '📷 Motion Detected';
      default:               return '🌿 Greenhouse Alert';
    }
  }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `flutter test app/test/models/cam_status_test.dart app/test/models/cam_event_test.dart app/test/models/notification_settings_test.dart app/test/models/weather_alert_test.dart`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/lib/models/cam_status.dart app/lib/models/cam_event.dart app/lib/models/notification_settings.dart app/lib/models/weather_alert.dart app/test/models/cam_status_test.dart app/test/models/cam_event_test.dart app/test/models/notification_settings_test.dart app/test/models/weather_alert_test.dart
git commit -m "feat: add camera status/event models and extend notification settings"
```

---

## Task 10: `MqttConnection` — camera topic routing + chunk raw events

**Files:**
- Modify: `app/lib/models/weather_events.dart`
- Modify: `app/lib/connection/mqtt_connection.dart`
- Modify: `app/test/connection/mqtt_connection_test.dart`

**Interfaces:**
- Consumes: nothing new from earlier app tasks (pure routing layer).
- Produces: `CamStatusRaw(payload)`, `CamEventChunkRaw(reqId, payload)`, `CamLiveFrameChunkRaw(payload)`; `MqttConnection.isCamStatusTopic`, `.isCamEventResponseTopic`, `.isCamLiveFrameTopic`. Task 11's repository consumes all of these.

- [ ] **Step 1: Write the failing tests (append to `app/test/connection/mqtt_connection_test.dart`)**

```dart
// --- append inside the existing group(...) block ---
    test('isCamStatusTopic matches only the status topic', () {
      expect(MqttConnection.isCamStatusTopic('greenhouse/cam/status'), isTrue);
      expect(MqttConnection.isCamStatusTopic('greenhouse/cam/live/frame'), isFalse);
    });

    test('isCamEventResponseTopic matches response/<id> and extracts the id', () {
      expect(MqttConnection.isCamEventResponseTopic('greenhouse/cam/event/response/req1'), isTrue);
      expect(MqttConnection.isCamEventResponseTopic('greenhouse/cam/event/request'), isFalse);
      expect(MqttConnection.extractCamEventReqId('greenhouse/cam/event/response/req1'), 'req1');
    });

    test('isCamLiveFrameTopic matches only the live frame topic', () {
      expect(MqttConnection.isCamLiveFrameTopic('greenhouse/cam/live/frame'), isTrue);
      expect(MqttConnection.isCamLiveFrameTopic('greenhouse/cam/live/start'), isFalse);
    });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `flutter test app/test/connection/mqtt_connection_test.dart`
Expected: FAIL — `isCamStatusTopic`/`isCamEventResponseTopic`/`extractCamEventReqId`/`isCamLiveFrameTopic` don't exist.

- [ ] **Step 3: Write the implementation**

Append to `app/lib/models/weather_events.dart`:

```dart
/// Carries the raw JSON payload from greenhouse/cam/status.
class CamStatusRaw {
  final String payload;
  const CamStatusRaw(this.payload);
}

/// Carries one chunk of a greenhouse/cam/event/response/<id> photo transfer.
class CamEventChunkRaw {
  final String reqId;
  final String payload; // JSON: {chunk, total, data} or {error}
  const CamEventChunkRaw(this.reqId, this.payload);
}

/// Carries one chunk of a greenhouse/cam/live/frame relayed live-view frame.
class CamLiveFrameChunkRaw {
  final String payload; // JSON: {frame_id, chunk, total, data}
  const CamLiveFrameChunkRaw(this.payload);
}
```

In `app/lib/connection/mqtt_connection.dart`, add the routing checks and route them in `_route`:

```dart
  void _route(String topic, String payload) {
    if (isWeatherAlertTopic(topic)) {
      try { _events.add(WeatherAlert.fromMqtt(payload)); } catch (_) {}
    } else if (isWeatherForecastTopic(topic)) {
      _events.add(WeatherForecastRaw(payload));
    } else if (isRulesCurrentTopic(topic)) {
      _events.add(RulesPayloadRaw(payload));
    } else if (isNotificationSettingsTopic(topic)) {
      _events.add(NotificationSettingsRaw(payload));
    } else if (isHistoryResponseTopic(topic)) {
      _events.add(HistoryResponseRaw(topic.substring(_historyResponsePrefix.length), payload));
    } else if (isCamStatusTopic(topic)) {
      _events.add(CamStatusRaw(payload));
    } else if (isCamEventResponseTopic(topic)) {
      _events.add(CamEventChunkRaw(extractCamEventReqId(topic), payload));
    } else if (isCamLiveFrameTopic(topic)) {
      _events.add(CamLiveFrameChunkRaw(payload));
    } else if (isSensorTopic(topic)) {
      try { _events.add(SensorReading.fromMqtt(topic, payload)); } catch (_) {}
    } else if (isNodeStatusTopic(topic)) {
      _events.add(NodeStatus.fromMqttStatus(extractNodeId(topic), payload));
    } else if (isNodeBatteryTopic(topic)) {
      _events.add(NodeStatus.fromMqttBattery(extractNodeId(topic), payload));
    } else if (isActuatorStateTopic(topic)) {
      _events.add(ActuatorState.fromMqttState(extractActuatorId(topic), payload));
    }
  }
```

Add the static helpers alongside the existing ones:

```dart
  static bool isCamStatusTopic(String t) => t == 'greenhouse/cam/status';

  static const _camEventResponsePrefix = 'greenhouse/cam/event/response/';
  static bool isCamEventResponseTopic(String t) => t.startsWith(_camEventResponsePrefix);
  static String extractCamEventReqId(String t) => t.substring(_camEventResponsePrefix.length);

  static bool isCamLiveFrameTopic(String t) => t == 'greenhouse/cam/live/frame';
```

Also add the new model import at the top of `mqtt_connection.dart`: `weather_events.dart` is already imported, so no new import line is needed (the new `*Raw` classes live in that same file).

- [ ] **Step 4: Run tests to verify they pass**

Run: `flutter test app/test/connection/mqtt_connection_test.dart`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/lib/models/weather_events.dart app/lib/connection/mqtt_connection.dart app/test/connection/mqtt_connection_test.dart
git commit -m "feat: route camera status/event/live-frame MQTT topics"
```

---

## Task 11: `GreenhouseRepository` — camera status, event-photo fetch, live start/stop/frames

**Files:**
- Modify: `app/lib/repository/greenhouse_repository.dart`
- Modify: `app/test/repository/greenhouse_repository_test.dart`

**Interfaces:**
- Consumes: `CamStatusRaw`/`CamEventChunkRaw`/`CamLiveFrameChunkRaw` (Task 10), `CamStatus`/`CamEvent` (Task 9).
- Produces: `GreenhouseRepository.camStatus` (`Stream<CamStatus>`), `.fetchEventPhoto(String eventId) -> Future<Uint8List?>`, `.startLive()`, `.stopLive()`, `.liveFrames` (`Stream<Uint8List>`). Task 12's Camera screen consumes all of these.

- [ ] **Step 1: Write the failing tests (append to `app/test/repository/greenhouse_repository_test.dart`)**

```dart
// --- append to app/test/repository/greenhouse_repository_test.dart ---
  test('camStatus stream emits parsed CamStatus from a CamStatusRaw event', () async {
    final future = repo.camStatus.first;
    eventsCtrl.add(const CamStatusRaw('{"online": true, "last_seen": 1700000000.0, "ip": "192.168.1.50", "last_event": null}'));
    final status = await future;
    expect(status.online, isTrue);
    expect(status.ip, '192.168.1.50');
  });

  test('fetchEventPhoto requests over MQTT and reassembles chunked response', () async {
    repo.connect(_config);
    final resultFuture = repo.fetchEventPhoto('evt1');

    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/cam/event/request', captureAny()),
    ).captured.single as String;
    final request = jsonDecode(capturedPayload) as Map<String, dynamic>;
    expect(request['event_id'], 'evt1');
    final reqId = request['id'] as String;

    final bytes = utf8.encode('fake-jpeg-bytes');
    final b64 = base64Encode(bytes);
    eventsCtrl.add(CamEventChunkRaw(reqId, jsonEncode({'chunk': 0, 'total': 1, 'data': b64})));

    final result = await resultFuture;
    expect(result, bytes);
  });

  test('fetchEventPhoto reassembles multiple chunks in order', () async {
    repo.connect(_config);
    final resultFuture = repo.fetchEventPhoto('evt1');
    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/cam/event/request', captureAny()),
    ).captured.single as String;
    final reqId = (jsonDecode(capturedPayload) as Map<String, dynamic>)['id'] as String;

    final part1 = base64Encode(utf8.encode('AAA'));
    final part2 = base64Encode(utf8.encode('BBB'));
    eventsCtrl.add(CamEventChunkRaw(reqId, jsonEncode({'chunk': 1, 'total': 2, 'data': part2})));
    eventsCtrl.add(CamEventChunkRaw(reqId, jsonEncode({'chunk': 0, 'total': 2, 'data': part1})));

    final result = await resultFuture;
    expect(utf8.decode(result!), 'AAABBB');
  });

  test('fetchEventPhoto returns null on a camera_unreachable error', () async {
    repo.connect(_config);
    final resultFuture = repo.fetchEventPhoto('evt1');
    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/cam/event/request', captureAny()),
    ).captured.single as String;
    final reqId = (jsonDecode(capturedPayload) as Map<String, dynamic>)['id'] as String;

    eventsCtrl.add(CamEventChunkRaw(reqId, jsonEncode({'error': 'camera_unreachable'})));

    expect(await resultFuture, isNull);
  });

  test('startLive publishes to greenhouse/cam/live/start', () async {
    await repo.startLive();
    verify(() => conn.publishRaw('greenhouse/cam/live/start', any())).called(1);
  });

  test('stopLive publishes to greenhouse/cam/live/stop', () async {
    await repo.stopLive();
    verify(() => conn.publishRaw('greenhouse/cam/live/stop', any())).called(1);
  });

  test('liveFrames emits reassembled frame bytes from chunked live-frame events', () async {
    final future = repo.liveFrames.first;
    final data = utf8.encode('one-frame-jpeg');
    eventsCtrl.add(CamLiveFrameChunkRaw(jsonEncode({
      'frame_id': 1, 'chunk': 0, 'total': 1, 'data': base64Encode(data),
    })));
    expect(await future, data);
  });
```

Also add the new model import to the top of `app/test/repository/greenhouse_repository_test.dart` (the test code above never names `Uint8List` directly, so no `dart:typed_data` import is needed there — only in the repository implementation itself, added below):

```dart
import 'package:greenhouse_app/models/cam_status.dart';
```

(`CamStatusRaw`/`CamEventChunkRaw`/`CamLiveFrameChunkRaw` already come from the existing `weather_events.dart` import.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `flutter test app/test/repository/greenhouse_repository_test.dart`
Expected: FAIL — `camStatus`/`fetchEventPhoto`/`startLive`/`stopLive`/`liveFrames` don't exist on `GreenhouseRepository`.

- [ ] **Step 3: Write the implementation**

Add imports at the top of `app/lib/repository/greenhouse_repository.dart`:

```dart
import 'dart:typed_data';
import 'package:greenhouse_app/models/cam_status.dart';
```

Add new controllers/state alongside the existing ones in the class body:

```dart
  final _camStatusCtrl = StreamController<CamStatus>.broadcast();
  final _camEventChunkCtrl = StreamController<CamEventChunkRaw>.broadcast();
  final _camLiveFrameCtrl = StreamController<Uint8List>.broadcast();

  // Buffers chunks per in-flight live frame_id until `total` chunks have
  // arrived, then emits the reassembled bytes and drops the buffer.
  final Map<int, List<String?>> _liveFrameBuffers = {};
```

Add these cases to `_handle`:

```dart
    } else if (event is CamStatusRaw) {
      try {
        _camStatusCtrl.add(CamStatus.fromJson(jsonDecode(event.payload) as Map<String, dynamic>));
      } catch (_) {}
    } else if (event is CamEventChunkRaw) {
      _camEventChunkCtrl.add(event);
    } else if (event is CamLiveFrameChunkRaw) {
      _handleLiveFrameChunk(event);
    }
```

(Insert this immediately before the final `else if (event is HistoryResponseRaw)` block's closing brace, i.e. as additional `else if` branches in the same chain.)

Add the live-frame reassembly helper and the new public members:

```dart
  void _handleLiveFrameChunk(CamLiveFrameChunkRaw event) {
    try {
      final data = jsonDecode(event.payload) as Map<String, dynamic>;
      final frameId = data['frame_id'] as int;
      final chunk = data['chunk'] as int;
      final total = data['total'] as int;
      final buffer = _liveFrameBuffers.putIfAbsent(frameId, () => List<String?>.filled(total, null));
      buffer[chunk] = data['data'] as String;
      if (buffer.every((c) => c != null)) {
        final bytes = buffer.map((c) => base64Decode(c!)).expand((b) => b).toList();
        _camLiveFrameCtrl.add(Uint8List.fromList(bytes));
        _liveFrameBuffers.remove(frameId);
      }
    } catch (_) {}
  }

  /// Fires whenever the Pi publishes an updated camera status (retained, so
  /// the app gets current state immediately on connect).
  Stream<CamStatus> get camStatus => _camStatusCtrl.stream;

  /// Fires with reassembled JPEG bytes for each relayed live-view frame,
  /// only while a live session is active (see startLive/stopLive).
  Stream<Uint8List> get liveFrames => _camLiveFrameCtrl.stream;

  /// Requests a motion-event photo over MQTT (mirrors fetchHistoryViaMqtt's
  /// request/response-by-id shape) and reassembles the chunked response.
  /// Returns null on timeout or if the Pi reports the camera unreachable.
  Future<Uint8List?> fetchEventPhoto(String eventId) async {
    final id = 'e${DateTime.now().microsecondsSinceEpoch}';
    final chunks = <String?>[];
    var total = -1;

    final completer = Completer<Uint8List?>();
    late final StreamSubscription<CamEventChunkRaw> sub;
    sub = _camEventChunkCtrl.stream.listen((event) {
      if (event.reqId != id) return;
      Map<String, dynamic> data;
      try {
        data = jsonDecode(event.payload) as Map<String, dynamic>;
      } catch (_) {
        return;
      }
      if (data.containsKey('error')) {
        sub.cancel();
        if (!completer.isCompleted) completer.complete(null);
        return;
      }
      if (total == -1) {
        total = data['total'] as int;
        chunks.addAll(List<String?>.filled(total, null));
      }
      chunks[data['chunk'] as int] = data['data'] as String;
      if (chunks.every((c) => c != null)) {
        sub.cancel();
        final bytes = chunks.map((c) => base64Decode(c!)).expand((b) => b).toList();
        if (!completer.isCompleted) completer.complete(Uint8List.fromList(bytes));
      }
    });

    await connection.publishRaw('greenhouse/cam/event/request', jsonEncode({'id': id, 'event_id': eventId}));

    return completer.future.timeout(const Duration(seconds: 15), onTimeout: () {
      sub.cancel();
      return null;
    });
  }

  /// Starts (or refreshes, if already active) the on-demand remote live
  /// relay. Call again periodically (~every 30s) while the live screen stays
  /// open — cam_bridge.py auto-stops the relay after 2 minutes without one.
  Future<void> startLive() => connection.publishRaw('greenhouse/cam/live/start', '1');

  Future<void> stopLive() => connection.publishRaw('greenhouse/cam/live/stop', '1');
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `flutter test app/test/repository/greenhouse_repository_test.dart`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/lib/repository/greenhouse_repository.dart app/test/repository/greenhouse_repository_test.dart
git commit -m "feat: add camera status/event-photo/live-relay support to the repository"
```

---

## Task 12: `CameraScreen` UI

**Files:**
- Create: `app/lib/providers/camera_provider.dart`
- Create: `app/lib/screens/camera/camera_screen.dart`
- Modify: `app/pubspec.yaml` (add `flutter_mjpeg`)
- Test: `app/test/screens/camera_screen_test.dart`

**Interfaces:**
- Consumes: `repositoryProvider` (existing), `connectionStatusProvider` (existing), `GreenhouseRepository.camStatus`/`.fetchEventPhoto`/`.startLive`/`.stopLive`/`.liveFrames` (Task 11), `CamStatus`/`CamEvent` (Task 9).
- Produces: `camStatusProvider` (`StreamProvider<CamStatus>`), `CameraScreen` widget. Task 13 wires this screen into navigation.

- [ ] **Step 1: Add the dependency**

In `app/pubspec.yaml`, alongside the other dependencies (after `fl_chart`):

```yaml
  flutter_mjpeg: ^2.0.0
```

Run: `cd app && flutter pub get`
Expected: resolves successfully, `pubspec.lock` updated.

- [ ] **Step 2: Write the provider**

```dart
// app/lib/providers/camera_provider.dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/cam_status.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';

/// Emits the current camera status (online/offline + last motion event).
final camStatusProvider = StreamProvider<CamStatus>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).camStatus;
});
```

- [ ] **Step 3: Write the failing widget test**

`camStatusProvider` is overridden directly with `StreamProvider.overrideWith`, rather than routing fake MQTT events through the full connection/repository stack — that plumbing is already covered independently by Tasks 9-11's unit tests, and `mqttConnectionProvider` is concretely typed as `Provider<MqttConnection>` (see `app/lib/providers/connection_provider.dart:12`), not an interface type a mock could substitute for. `connectionStatusProvider` is overridden the same way, so the widget test never touches `pairingServiceProvider`/secure storage at all.

```dart
// app/test/screens/camera_screen_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/cam_event.dart';
import 'package:greenhouse_app/models/cam_status.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/providers/camera_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/screens/camera/camera_screen.dart';

void main() {
  testWidgets('shows offline status when camera has never been seen', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        camStatusProvider.overrideWith((ref) => Stream.value(const CamStatus(online: false))),
        connectionStatusProvider.overrideWith((ref) => Stream.value(ConnectionStatus.offline)),
      ],
      child: const MaterialApp(home: CameraScreen()),
    ));
    await tester.pump();
    expect(find.textContaining('Offline'), findsWidgets);
  });

  testWidgets('shows online status with last motion event', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        camStatusProvider.overrideWith((ref) => Stream.value(CamStatus(
              online: true,
              ip: '192.168.1.50',
              lastEvent: const CamEvent(eventId: 'evt1', ts: 1700000000),
            ))),
        connectionStatusProvider.overrideWith((ref) => Stream.value(ConnectionStatus.local)),
      ],
      child: const MaterialApp(home: CameraScreen()),
    ));
    await tester.pump();
    expect(find.textContaining('Online'), findsWidgets);
    expect(find.textContaining('Last motion'), findsOneWidget);
  });
}
```

- [ ] **Step 4: Run test to verify it fails, then write the real implementation**

Run: `flutter test app/test/screens/camera_screen_test.dart`
Expected: FAIL — `camera_screen.dart` and `camera_provider.dart` don't exist yet.

```dart
// app/lib/screens/camera/camera_screen.dart
import 'package:flutter/material.dart';
import 'package:flutter_mjpeg/flutter_mjpeg.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/cam_status.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/providers/camera_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class CameraScreen extends ConsumerStatefulWidget {
  const CameraScreen({super.key});
  @override
  ConsumerState<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends ConsumerState<CameraScreen> {
  @override
  Widget build(BuildContext context) {
    final camStatus = ref.watch(camStatusProvider).valueOrNull;
    final connectionStatus = ref.watch(connectionStatusProvider).valueOrNull;

    return Scaffold(
      appBar: AppBar(title: const Text('Camera')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _StatusCard(status: camStatus),
          const SizedBox(height: 12),
          _LiveViewCard(
            connectionStatus: connectionStatus,
            cameraIp: camStatus?.ip,
          ),
        ],
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  final CamStatus? status;
  const _StatusCard({required this.status});

  @override
  Widget build(BuildContext context) {
    final online = status?.online ?? false;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(online ? Icons.videocam : Icons.videocam_off,
                color: online ? AppColors.brand : Colors.grey),
            const SizedBox(width: 8),
            Text(online ? 'Camera: Online' : 'Camera: Offline',
                style: const TextStyle(fontWeight: FontWeight.bold)),
          ]),
          if (status?.lastEvent != null) ...[
            const SizedBox(height: 8),
            Text('Last motion: ${status!.lastEvent!.timestamp}',
                style: const TextStyle(fontSize: 12, color: Colors.grey)),
          ],
        ]),
      ),
    );
  }
}

class _LiveViewCard extends ConsumerStatefulWidget {
  final ConnectionStatus? connectionStatus;
  final String? cameraIp;
  const _LiveViewCard({required this.connectionStatus, required this.cameraIp});

  @override
  ConsumerState<_LiveViewCard> createState() => _LiveViewCardState();
}

class _LiveViewCardState extends ConsumerState<_LiveViewCard> {
  @override
  void dispose() {
    if (widget.connectionStatus == ConnectionStatus.remote) {
      ref.read(repositoryProvider).stopLive();
    }
    super.dispose();
  }

  @override
  void didUpdateWidget(_LiveViewCard old) {
    super.didUpdateWidget(old);
    if (widget.connectionStatus == ConnectionStatus.remote &&
        old.connectionStatus != ConnectionStatus.remote) {
      ref.read(repositoryProvider).startLive();
    } else if (widget.connectionStatus != ConnectionStatus.remote &&
        old.connectionStatus == ConnectionStatus.remote) {
      ref.read(repositoryProvider).stopLive();
    }
  }

  @override
  void initState() {
    super.initState();
    if (widget.connectionStatus == ConnectionStatus.remote) {
      ref.read(repositoryProvider).startLive();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (widget.connectionStatus == ConnectionStatus.local && widget.cameraIp != null) {
      return Card(
        clipBehavior: Clip.antiAlias,
        child: Mjpeg(stream: 'http://${widget.cameraIp}/stream', isLive: true),
      );
    }
    if (widget.connectionStatus == ConnectionStatus.remote) {
      return Card(
        child: Column(children: [
          StreamBuilder<Uint8List>(
            stream: ref.watch(repositoryProvider).liveFrames,
            builder: (context, snap) {
              if (!snap.hasData) {
                return const SizedBox(
                  height: 240,
                  child: Center(child: CircularProgressIndicator()),
                );
              }
              return Image.memory(snap.data!, gaplessPlayback: true);
            },
          ),
          const Padding(
            padding: EdgeInsets.all(8),
            child: Text('Refreshing ~1x/sec (remote view)',
                style: TextStyle(fontSize: 11, color: Colors.grey)),
          ),
        ]),
      );
    }
    return const Card(
      child: Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: Text('Camera unavailable — not connected.')),
      ),
    );
  }
}
```

Add `import 'dart:typed_data';` to the top of `camera_screen.dart` for the `Uint8List` reference in `_LiveViewCardState.build`.

- [ ] **Step 5: Run test to verify it passes**

Run: `flutter test app/test/screens/camera_screen_test.dart`
Expected: passed

- [ ] **Step 6: Commit**

```bash
git add app/lib/providers/camera_provider.dart app/lib/screens/camera/camera_screen.dart app/pubspec.yaml app/pubspec.lock app/test/screens/camera_screen_test.dart
git commit -m "feat: add Camera screen with LAN/remote live view and status card"
```

---

## Task 13: Wire into navigation and the alert-settings toggle

**Files:**
- Modify: `app/lib/app.dart:8-46`
- Modify: `app/lib/screens/shell_screen.dart`
- Modify: `app/lib/screens/weather/weather_screen.dart:556-587` (`_AlertSettingsCard`)

**Interfaces:**
- Consumes: `CameraScreen` (Task 12), `NotificationSettings.motionAlert` (Task 9).
- Produces: a reachable `/camera` route and bottom-nav destination; a third toggle switch in the existing alert-settings card.

- [ ] **Step 1: Add the route**

In `app/lib/app.dart`, add the import:

```dart
import 'package:greenhouse_app/screens/camera/camera_screen.dart';
```

Add the route inside the `ShellRoute`'s `routes` list, alongside the existing five:

```dart
        GoRoute(path: '/camera',    builder: (_, __) => const CameraScreen()),
```

- [ ] **Step 2: Add the bottom-nav destination**

In `app/lib/screens/shell_screen.dart`, add `/camera` to `_routes` and a matching `NavigationDestination`:

```dart
  static const _routes = ['/dashboard', '/devices', '/control', '/camera', '/weather', '/settings'];
```

```dart
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard),    label: 'Dashboard'),
          NavigationDestination(icon: Icon(Icons.sensors),      label: 'Devices'),
          NavigationDestination(icon: Icon(Icons.toggle_on),    label: 'Control'),
          NavigationDestination(icon: Icon(Icons.videocam),     label: 'Camera'),
          NavigationDestination(icon: Icon(Icons.cloud),        label: 'Weather'),
          NavigationDestination(icon: Icon(Icons.settings),     label: 'Settings'),
        ],
```

- [ ] **Step 3: Add the motion-alert toggle**

In `app/lib/screens/weather/weather_screen.dart`'s `_AlertSettingsCard` (around line 556-587), add a third `SwitchListTile`:

```dart
    return Card(
      margin: const EdgeInsets.fromLTRB(8, 4, 8, 8),
      child: Column(
        children: [
          SwitchListTile(
            key: const Key('alert-settings-frost-switch'),
            title: const Text('Frost forecast alerts'),
            value: settings.frostForecast,
            onChanged: (v) => publish(settings.copyWith(frostForecast: v)),
          ),
          SwitchListTile(
            key: const Key('alert-settings-daily-switch'),
            title: const Text('Daily weather summary'),
            value: settings.dailySummary,
            onChanged: (v) => publish(settings.copyWith(dailySummary: v)),
          ),
          SwitchListTile(
            key: const Key('alert-settings-motion-switch'),
            title: const Text('Camera motion alerts'),
            value: settings.motionAlert,
            onChanged: (v) => publish(settings.copyWith(motionAlert: v)),
          ),
        ],
      ),
    );
```

Also update the default fallback a few lines above this widget's `build` (where `settingsAsync.value ?? const NotificationSettings(...)` is constructed) to include `motionAlert: true`:

```dart
    final settings = settingsAsync.value ??
        const NotificationSettings(frostForecast: true, dailySummary: true, motionAlert: true);
```

- [ ] **Step 4: Manual verification (no new automated test — this task is pure wiring of already-tested pieces)**

Run: `cd app && flutter analyze`
Expected: no new errors.

Run: `cd app && flutter test`
Expected: full existing suite plus all tests added in Tasks 9-12 pass.

- [ ] **Step 5: Commit**

```bash
git add app/lib/app.dart app/lib/screens/shell_screen.dart app/lib/screens/weather/weather_screen.dart
git commit -m "feat: wire the Camera screen into navigation and add the motion-alert toggle"
```

---

## Self-Review Notes

- **Spec coverage:** every MVP goal in the design spec has a task — LAN live view (Task 12), Pi motion detection (Tasks 1/3), push alert reuse (Task 3, extending `WeatherAlert`/`send_push`), event photo fetch-on-tap (Tasks 4/11/12), MQTT remote live fallback with on-demand start/stop and auto-timeout (Tasks 5/11/12), status card (Tasks 4/6/9/12), 7-day pruning (Tasks 2/6), install/systemd wiring (Task 7), firmware (Task 8). Phase 2 (WebRTC) is intentionally not planned here, per the spec and the user's explicit instruction.
- **Type/name consistency checked:** `_publish_chunked`/`publish_status`/`_get_camera_ip` names introduced in Task 3-4 are reused verbatim in Tasks 5-6; `CamStatus`/`CamEvent` (Task 9) field names (`online`, `ip`, `lastEvent`, `eventId`, `ts`) match what Task 11's `fromJson` calls and Task 12's UI reference exactly.
- **Verified rather than assumed (Task 12, Step 3):** confirmed against the actual file that `mqttConnectionProvider` is concretely typed (`Provider<MqttConnection>`), so the widget test overrides `camStatusProvider`/`connectionStatusProvider` directly instead of trying to substitute a mock connection there.
- **Fixed during self-review:** `pi/tests/test_cam_bridge.py` originally used `__import__('cam_store')` inline and was missing a top-level `import time` despite several appended tests calling `time.time()`/`time.sleep()` — both fixed by adding proper imports in Task 3's initial file header instead of leaving them to surface as failures partway through Task 4/5/6.
