# pi/tests/test_cam_bridge.py
import io
import json
import os
import sys
import threading
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


# POST /cam/frame is token-gated (see cam_bridge._token_ok) — every test that
# exercises the happy path has to authenticate like the real camera does.
_TEST_TOKEN = 'test-cam-token'


def _fresh_client(tmp_path, monkeypatch):
    monkeypatch.setattr(cam_bridge, '_db_conn',
                         cam_store.init_db(str(tmp_path / 'cam.db')))
    monkeypatch.setattr(cam_bridge, '_prev_gray', None)
    monkeypatch.setattr(cam_bridge, '_camera_ip', None)
    monkeypatch.setattr(cam_bridge, '_last_seen', 0.0)
    monkeypatch.setattr(cam_bridge, '_last_event', None)
    monkeypatch.setattr(cam_bridge, 'send_push', lambda *a, **k: None)
    monkeypatch.setattr(cam_bridge, '_mqtt_client', None)
    monkeypatch.setattr(cam_bridge, 'CAM_TOKEN', _TEST_TOKEN)
    cam_bridge._rate_buckets.clear()   # rate limiter is process-global state
    return cam_bridge.app.test_client()


def _post_frame(client, body, **kwargs):
    """POST a frame with the camera's auth header, as the firmware does."""
    headers = kwargs.pop('headers', {})
    headers.setdefault('X-Cam-Token', _TEST_TOKEN)
    return client.post('/cam/frame', data=body, content_type='image/jpeg',
                       headers=headers, **kwargs)


def test_cam_url_appends_token_query_param(monkeypatch):
    monkeypatch.setattr(cam_bridge, 'CAM_TOKEN', 'sekret')
    assert cam_bridge._cam_url('192.168.1.50', '/capture') == 'http://192.168.1.50/capture?token=sekret'
    assert (cam_bridge._cam_url('192.168.1.50', '/event/evt1')
            == 'http://192.168.1.50/event/evt1?token=sekret')


def test_load_cam_token_defaults_to_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cam_bridge, 'CAM_TOKEN_FILE', str(tmp_path / 'missing.txt'))
    assert cam_bridge._load_cam_token() == ''


def test_load_cam_token_strips_whitespace(tmp_path, monkeypatch):
    token_file = tmp_path / 'cam_token.txt'
    token_file.write_text('  sekret-value\n')
    monkeypatch.setattr(cam_bridge, 'CAM_TOKEN_FILE', str(token_file))
    assert cam_bridge._load_cam_token() == 'sekret-value'


def test_cam_frame_discards_first_frame_no_prior_baseline(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    resp = _post_frame(client, _jpeg(100))
    assert resp.status_code == 200
    assert resp.data == b'discard'


def test_cam_frame_saves_on_large_change(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    _post_frame(client, _jpeg(10))
    resp = _post_frame(client, _jpeg(250))
    assert resp.data.startswith(b'save:')


def test_cam_frame_discards_on_small_change(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    _post_frame(client, _jpeg(100))
    resp = _post_frame(client, _jpeg(101))
    assert resp.data == b'discard'


def test_cam_frame_updates_heartbeat_and_camera_ip(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    _post_frame(client, _jpeg(100),
                environ_overrides={'REMOTE_ADDR': '192.168.1.50'})
    assert cam_bridge._get_camera_ip() == '192.168.1.50'


def test_cam_frame_empty_body_discards_without_crashing(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    resp = _post_frame(client, b'')
    assert resp.data == b'discard'


def test_motion_fires_push_when_setting_enabled(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cam_bridge, 'send_push', lambda title, body: calls.append((title, body)))
    monkeypatch.setattr(cam_bridge, '_load_motion_alert_setting', lambda: True)
    _post_frame(client, _jpeg(10))
    _post_frame(client, _jpeg(250))
    assert len(calls) == 1
    assert calls[0][0] == 'Motion detected'


def test_motion_skips_push_when_setting_disabled(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cam_bridge, 'send_push', lambda title, body: calls.append((title, body)))
    monkeypatch.setattr(cam_bridge, '_load_motion_alert_setting', lambda: False)
    _post_frame(client, _jpeg(10))
    _post_frame(client, _jpeg(250))
    assert calls == []


def test_load_motion_alert_setting_defaults_true_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cam_bridge, 'NOTIFICATION_SETTINGS_CFG', str(tmp_path / 'missing.json'))
    assert cam_bridge._load_motion_alert_setting() is True


def test_load_motion_alert_setting_reads_false_from_file(tmp_path, monkeypatch):
    cfg = tmp_path / 'settings.json'
    cfg.write_text(json.dumps({'frost_forecast': True, 'daily_summary': True, 'motion_alert': False}))
    monkeypatch.setattr(cam_bridge, 'NOTIFICATION_SETTINGS_CFG', str(cfg))
    assert cam_bridge._load_motion_alert_setting() is False


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


def test_prune_expired_events_deletes_on_camera_then_from_db(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, '_camera_ip', '192.168.1.50')
    cam_store.record_event(cam_bridge._db_conn, 'old', 1, 20.0)
    deleted_urls = []
    monkeypatch.setattr(cam_bridge, 'urlopen', lambda req, timeout=5: deleted_urls.append(req.full_url))
    cam_bridge._prune_expired_events(cam_bridge._db_conn)
    assert deleted_urls == [cam_bridge._cam_url('192.168.1.50', '/event/old')]
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


# ── POST /cam/frame authentication (the camera-hijack hole) ──────────────────
# Before this gate existed, any LAN host could POST here and (a) become "the
# camera" as far as the Pi was concerned, (b) receive CAM_TOKEN on the Pi's
# next /capture or /event fetch, and (c) spam motion events/push alerts.

def test_cam_frame_rejects_request_with_no_token(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    resp = client.post('/cam/frame', data=_jpeg(100), content_type='image/jpeg')
    assert resp.status_code == 401


def test_cam_frame_rejects_wrong_token(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    resp = client.post('/cam/frame', data=_jpeg(100), content_type='image/jpeg',
                       headers={'X-Cam-Token': 'wrong'})
    assert resp.status_code == 401


def test_cam_frame_accepts_token_via_query_param(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    resp = client.post(f'/cam/frame?token={_TEST_TOKEN}', data=_jpeg(100),
                       content_type='image/jpeg')
    assert resp.status_code == 200


def test_unauthenticated_frame_cannot_hijack_camera_ip(tmp_path, monkeypatch):
    """The core of the vulnerability: a rejected POST must not move _camera_ip,
    or the Pi would send CAM_TOKEN to the attacker on its next fetch."""
    client = _fresh_client(tmp_path, monkeypatch)
    client.post('/cam/frame', data=_jpeg(100), content_type='image/jpeg',
                environ_overrides={'REMOTE_ADDR': '10.0.0.66'})
    assert cam_bridge._get_camera_ip() is None


def test_unauthenticated_frame_cannot_create_motion_event(tmp_path, monkeypatch):
    """A rejected POST must not reach motion detection — otherwise an attacker
    could flood push notifications and grow the events DB at will."""
    client = _fresh_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cam_bridge, 'send_push', lambda t, b: calls.append((t, b)))
    monkeypatch.setattr(cam_bridge, '_load_motion_alert_setting', lambda: True)
    _post_frame(client, _jpeg(10))                      # legit baseline
    client.post('/cam/frame', data=_jpeg(250),          # attacker's big change
                content_type='image/jpeg')
    assert calls == []


def test_token_check_fails_closed_when_no_token_provisioned(tmp_path, monkeypatch):
    """An empty/missing cam_token.txt must not mean 'allow everyone'."""
    client = _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, 'CAM_TOKEN', '')
    resp = client.post('/cam/frame', data=_jpeg(100), content_type='image/jpeg',
                       headers={'X-Cam-Token': ''})
    assert resp.status_code == 401
    assert cam_bridge._token_ok('') is False
    assert cam_bridge._token_ok('anything') is False


def test_cam_frame_rejects_oversized_body(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    oversized = b'\xff' * (cam_bridge.MAX_FRAME_BYTES + 1)
    resp = _post_frame(client, oversized)
    assert resp.status_code == 413


# ── Frame-intake rate limiting ───────────────────────────────────────────────
# The real camera POSTs every ~3s. Anything far above that is a malfunction or
# an attacker; the cap bounds both token brute-forcing and request flooding.

def test_frame_intake_is_rate_limited(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    codes = [_post_frame(client, _jpeg(100)).status_code
             for _ in range(cam_bridge._MAX_FRAMES_PER_WINDOW + 5)]
    assert 429 in codes
    assert codes[0] == 200          # legitimate cadence is unaffected


def test_rate_limit_applies_before_token_check(tmp_path, monkeypatch):
    """Unauthenticated flooding must be cheap to reject — the limiter runs
    before any per-request crypto work is done for the attacker."""
    client = _fresh_client(tmp_path, monkeypatch)
    codes = []
    for _ in range(cam_bridge._MAX_FRAMES_PER_WINDOW + 5):
        codes.append(client.post('/cam/frame', data=_jpeg(100),
                                 content_type='image/jpeg').status_code)
    assert codes[0] == 401          # still rejected on auth...
    assert codes[-1] == 429         # ...and eventually throttled too


def test_rate_limit_is_per_client(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    for _ in range(cam_bridge._MAX_FRAMES_PER_WINDOW + 5):
        _post_frame(client, _jpeg(100),
                    environ_overrides={'REMOTE_ADDR': '10.0.0.66'})
    # A flooding host must not throttle the real camera on another address.
    resp = _post_frame(client, _jpeg(100),
                       environ_overrides={'REMOTE_ADDR': '192.168.1.50'})
    assert resp.status_code == 200


def test_rate_limit_window_resets(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    for _ in range(cam_bridge._MAX_FRAMES_PER_WINDOW + 5):
        _post_frame(client, _jpeg(100))
    assert _post_frame(client, _jpeg(100)).status_code == 429
    for bucket in cam_bridge._rate_buckets.values():   # fast-forward the window
        bucket[1] -= cam_bridge._RATE_WINDOW_SECONDS + 1
    assert _post_frame(client, _jpeg(100)).status_code == 200


def test_rate_bucket_table_is_bounded(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    for i in range(400):
        _post_frame(client, _jpeg(100),
                    environ_overrides={'REMOTE_ADDR': f'10.7.{i // 250}.{i % 250}'})
    assert len(cam_bridge._rate_buckets) <= cam_bridge._MAX_RATE_BUCKETS


# ── Signed frame POSTs (keeps CAM_TOKEN off a plaintext link) ────────────────
# The camera link has no TLS, so a bearer token was readable by any passive
# sniffer — and that token also authorizes DELETE /event/<id> on the camera.
# Signing proves knowledge of it without transmitting it.

def _sign(body, token=_TEST_TOKEN, ts=None):
    import hashlib, hmac as _hmac, time as _time
    ts = ts or str(int(_time.time()))
    msg = f"{ts}.{hashlib.sha256(body).hexdigest()}"
    return ts, _hmac.new(token.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _post_signed(client, body, **kw):
    ts, sig = _sign(body, **kw)
    return client.post('/cam/frame', data=body, content_type='image/jpeg',
                       headers={'X-Cam-Timestamp': ts, 'X-Cam-Signature': sig})


def test_signed_frame_is_accepted(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    assert _post_signed(client, _jpeg(100)).status_code == 200


def test_signature_is_not_replayable(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    body = _jpeg(100)
    ts, sig = _sign(body)
    hdrs = {'X-Cam-Timestamp': ts, 'X-Cam-Signature': sig}
    first = client.post('/cam/frame', data=body, content_type='image/jpeg', headers=hdrs)
    replay = client.post('/cam/frame', data=body, content_type='image/jpeg', headers=hdrs)
    assert first.status_code == 200
    assert replay.status_code == 401


def test_signature_is_bound_to_the_body(tmp_path, monkeypatch):
    """A captured signature must not authorize a different payload."""
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    ts, sig = _sign(_jpeg(100))
    resp = client.post('/cam/frame', data=_jpeg(250), content_type='image/jpeg',
                       headers={'X-Cam-Timestamp': ts, 'X-Cam-Signature': sig})
    assert resp.status_code == 401


def test_signature_from_wrong_token_rejected(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    assert _post_signed(client, _jpeg(100), token='not-the-token').status_code == 401


def test_stale_timestamp_rejected(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    old = str(int(time.time()) - cam_bridge.SIGNATURE_WINDOW_SECONDS - 60)
    assert _post_signed(client, _jpeg(100), ts=old).status_code == 401


def test_malformed_timestamp_rejected(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    resp = client.post('/cam/frame', data=_jpeg(100), content_type='image/jpeg',
                       headers={'X-Cam-Timestamp': 'not-a-number',
                                'X-Cam-Signature': 'deadbeef'})
    assert resp.status_code == 401


def test_unsigned_bearer_rejected_when_hardening_enabled(tmp_path, monkeypatch):
    """Once every camera is reflashed with signing firmware, the operator can
    refuse the token-in-the-clear path entirely."""
    client = _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, 'ALLOW_UNSIGNED_CAM_AUTH', False)
    assert _post_frame(client, _jpeg(100)).status_code == 401
    cam_bridge._seen_signatures.clear()
    assert _post_signed(client, _jpeg(100)).status_code == 200


def test_unsigned_bearer_still_works_by_default(tmp_path, monkeypatch):
    """Default must not brick a camera that hasn't been reflashed yet."""
    client = _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, 'ALLOW_UNSIGNED_CAM_AUTH', True)
    assert _post_frame(client, _jpeg(100)).status_code == 200


def test_seen_signature_table_is_bounded(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    monkeypatch.setattr(cam_bridge, '_MAX_FRAMES_PER_WINDOW', 10_000)
    for i in range(cam_bridge._MAX_SEEN_SIGNATURES + 50):
        _post_signed(client, _jpeg(i % 200))
    assert len(cam_bridge._seen_signatures) <= cam_bridge._MAX_SEEN_SIGNATURES


# ── Frame encryption (AES-256-GCM) ──────────────────────────────────────────
# Signing keeps the token off the wire; encryption keeps the greenhouse images
# off it too, without needing TLS on the ESP32.

def _encrypt(body, token=_TEST_TOKEN):
    import hashlib as _h, hmac as _hm, os as _os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _hm.new(token.encode(), cam_bridge.FRAME_KEY_INFO, _h.sha256).digest()
    nonce = _os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, body, None)


def _post_encrypted(client, body, token=_TEST_TOKEN, sign_token=_TEST_TOKEN):
    blob = _encrypt(body, token)
    ts, sig = _sign(blob, token=sign_token)
    return client.post('/cam/frame', data=blob, content_type='image/jpeg',
                       headers={'X-Cam-Timestamp': ts, 'X-Cam-Signature': sig,
                                'X-Cam-Encrypted': 'aes-256-gcm'})


def test_encrypted_frame_is_decrypted_and_processed(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    assert _post_encrypted(client, _jpeg(10)).status_code == 200
    resp = _post_encrypted(client, _jpeg(250))     # big change -> motion
    assert resp.data.startswith(b'save:')


def test_encrypted_frame_with_wrong_key_is_rejected(tmp_path, monkeypatch):
    """GCM authenticates on decrypt, so a wrongly-keyed payload never reaches
    the motion detector as garbage."""
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    resp = _post_encrypted(client, _jpeg(100), token='wrong-key',
                           sign_token=_TEST_TOKEN)
    assert resp.status_code == 401


def test_tampered_ciphertext_is_rejected(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    blob = bytearray(_encrypt(_jpeg(100)))
    blob[20] ^= 0xFF                                # flip a ciphertext bit
    ts, sig = _sign(bytes(blob))
    resp = client.post('/cam/frame', data=bytes(blob), content_type='image/jpeg',
                       headers={'X-Cam-Timestamp': ts, 'X-Cam-Signature': sig,
                                'X-Cam-Encrypted': 'aes-256-gcm'})
    assert resp.status_code == 401


def test_truncated_encrypted_payload_is_rejected(tmp_path, monkeypatch):
    _fresh_client(tmp_path, monkeypatch)
    assert cam_bridge._decrypt_frame(b'short') is None


def test_ciphertext_does_not_contain_the_plaintext(tmp_path, monkeypatch):
    """The actual point: a sniffer must not be able to read the image."""
    monkeypatch.setattr(cam_bridge, 'CAM_TOKEN', _TEST_TOKEN)
    plain = _jpeg(100)
    blob = _encrypt(plain)
    assert plain not in blob
    assert blob[:2] != b'\xff\xd8'                  # no JPEG magic on the wire
    assert cam_bridge._decrypt_frame(blob) == plain  # round-trips for the Pi


def test_plaintext_frames_refused_when_encryption_required(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, 'REQUIRE_ENCRYPTED_CAM', True)
    cam_bridge._seen_signatures.clear()
    assert _post_signed(client, _jpeg(100)).status_code == 401   # plaintext
    cam_bridge._seen_signatures.clear()
    assert _post_encrypted(client, _jpeg(100)).status_code == 200


def test_plaintext_still_accepted_by_default(tmp_path, monkeypatch):
    """Default must not brick a camera running pre-encryption firmware."""
    client = _fresh_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cam_bridge, 'REQUIRE_ENCRYPTED_CAM', False)
    cam_bridge._seen_signatures.clear()
    assert _post_signed(client, _jpeg(100)).status_code == 200


def test_encrypted_frame_fails_closed_without_aes_support(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    cam_bridge._seen_signatures.clear()
    monkeypatch.setattr(cam_bridge, '_AESGCM_AVAILABLE', False)
    assert _post_encrypted(client, _jpeg(100)).status_code == 503
