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
