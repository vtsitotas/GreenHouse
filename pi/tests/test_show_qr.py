import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import show_qr


def _write(tmp_path, monkeypatch, device=None, hivemq=None, cam_token=None):
    if device is not None:
        p = tmp_path / 'device.json'
        p.write_text(json.dumps(device))
        monkeypatch.setattr(show_qr, 'DEVICE_CFG', str(p))
    else:
        monkeypatch.setattr(show_qr, 'DEVICE_CFG', str(tmp_path / 'absent.json'))
    if hivemq is not None:
        p = tmp_path / 'hivemq.json'
        p.write_text(json.dumps(hivemq))
        monkeypatch.setattr(show_qr, 'HIVEMQ_CFG', str(p))
    else:
        monkeypatch.setattr(show_qr, 'HIVEMQ_CFG', str(tmp_path / 'absent2.json'))
    if cam_token is not None:
        p = tmp_path / 'cam_token.txt'
        p.write_text(cam_token)
        monkeypatch.setattr(show_qr, 'CAM_TOKEN_FILE', str(p))
    else:
        monkeypatch.setattr(show_qr, 'CAM_TOKEN_FILE', str(tmp_path / 'absent.txt'))


def test_payload_matches_the_app_ConnectionConfig_field_set(tmp_path, monkeypatch):
    """Every key app/lib/models/connection_config.dart's fromJson reads must be
    present -- this is the regression test for the bug this rewrite fixes: the
    old script's payload silently omitted api_token/cam_token entirely, so a
    QR from it paired successfully but left the app's apiToken empty (the
    exact cause of the history_auth_failure false-alert bug)."""
    _write(tmp_path, monkeypatch,
           device={'port': 8883, 'tls_fingerprint': 'AA:BB,CC:DD',
                   'username': 'app', 'password': 'realpw123',
                   'api_token': 'realtoken32chars'},
           hivemq={'host': 'xxx.s1.eu.hivemq.cloud', 'username': 'hmuser', 'password': 'hmpw'},
           cam_token='realcamtoken')
    payload = show_qr.build_payload(lan_override='10.0.0.5')

    assert payload == {
        'host_lan': '10.0.0.5',
        'host_remote': 'xxx.s1.eu.hivemq.cloud',
        'port': 8883,
        'tls_fingerprint': 'AA:BB,CC:DD',
        'username': 'app',
        'password': 'realpw123',
        'remote_username': 'hmuser',
        'remote_password': 'hmpw',
        'api_token': 'realtoken32chars',
        'cam_token': 'realcamtoken',
    }


def test_missing_hivemq_and_cam_token_degrade_to_empty_not_a_crash(tmp_path, monkeypatch):
    """A unit with no remote access configured and no camera still needs a
    working QR for local pairing -- matches portal.py's own _load_hivemq()/
    _load_cam_token() graceful-empty behavior."""
    _write(tmp_path, monkeypatch,
           device={'port': 8883, 'tls_fingerprint': 'AA:BB', 'username': 'app', 'password': 'pw'})
    payload = show_qr.build_payload(lan_override='10.0.0.5')

    assert payload['host_remote'] == ''
    assert payload['remote_username'] == ''
    assert payload['remote_password'] == ''
    assert payload['cam_token'] == ''


def test_lan_override_wins_over_auto_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(show_qr, '_detect_lan_ip', lambda: '192.168.1.99')
    _write(tmp_path, monkeypatch, device={'password': 'pw'})

    assert show_qr.build_payload(lan_override='10.0.0.5')['host_lan'] == '10.0.0.5'
    assert show_qr.build_payload(lan_override=None)['host_lan'] == '192.168.1.99'


def test_detect_lan_ip_falls_back_to_loopback_instead_of_raising(monkeypatch):
    """No route to the internet (e.g. a hotspot with no data pass-through)
    must not crash the tool -- the fallback is visibly wrong (127.0.0.1),
    which is easy to notice and override with --lan, rather than a stack
    trace that blocks generating a QR at all."""
    import socket

    class _NoRoute(socket.socket):
        def connect(self, *_a, **_kw):
            raise OSError('Network is unreachable')

    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: _NoRoute(*a, **kw))
    assert show_qr._detect_lan_ip() == '127.0.0.1'


def test_defaults_are_safe_when_device_json_itself_is_missing(tmp_path, monkeypatch):
    """A unit that hasn't finished first-boot provisioning yet must still
    produce a (visibly incomplete) payload rather than crash the tool."""
    _write(tmp_path, monkeypatch)  # no device, no hivemq, no cam_token
    payload = show_qr.build_payload(lan_override='10.0.0.5')

    assert payload['password'] == ''
    assert payload['tls_fingerprint'] == ''
    assert payload['username'] == 'app'
    assert payload['port'] == 8883
