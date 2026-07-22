import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'portal'))
import portal


@pytest.fixture(autouse=True)
def _reset_pair_state(monkeypatch):
    portal._pair_fail_count = 0
    portal._pair_locked = False
    # The 1s throttle in pair_confirm() is real production behavior we don't
    # need to pay for in every test run.
    monkeypatch.setattr(portal.time, 'sleep', lambda *_a, **_kw: None)
    yield
    portal._pair_fail_count = 0
    portal._pair_locked = False


def _write_config(tmp_path, pair_pin="123456"):
    config = tmp_path / "device.json"
    config.write_text(json.dumps({
        "device_id": "ABCDE",
        "username": "app",
        "password": "s3cr3t",
        "port": 8883,
        "tls_fingerprint": "AA:BB",
        "pair_pin": pair_pin,
    }))
    return str(config)


def test_pair_reports_found_without_secrets():
    client = portal.app.test_client()
    resp = client.get('/pair')
    assert resp.status_code == 200
    assert json.loads(resp.data) == {"found": True}


def test_pair_expired_window_returns_403(monkeypatch):
    monkeypatch.setattr(portal, '_START_TIME', 0)
    client = portal.app.test_client()
    resp = client.get('/pair')
    assert resp.status_code == 403


def test_pair_confirm_correct_pin_returns_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {
        "host": "xxx.hivemq.cloud", "username": "remote", "password": "remotepass",
    })
    client = portal.app.test_client()
    resp = client.post('/pair/confirm', json={"pin": "123456"})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["username"] == "app"
    assert data["password"] == "s3cr3t"
    assert data["tls_fingerprint"] == "AA:BB"
    assert data["host_remote"] == "xxx.hivemq.cloud"
    assert data["remote_username"] == "remote"
    assert data["remote_password"] == "remotepass"


def test_pair_confirm_wrong_pin_returns_401_and_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    resp = client.post('/pair/confirm', json={"pin": "000000"})
    assert resp.status_code == 401
    assert portal._pair_fail_count == 1


def test_pair_confirm_missing_pin_treated_as_wrong(monkeypatch, tmp_path):
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    resp = client.post('/pair/confirm', json={})
    assert resp.status_code == 401


def test_pair_confirm_locks_after_max_attempts_even_with_correct_pin(monkeypatch, tmp_path):
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    for _ in range(portal.MAX_PAIR_ATTEMPTS):
        resp = client.post('/pair/confirm', json={"pin": "000000"})
        assert resp.status_code == 401
    resp = client.post('/pair/confirm', json={"pin": "123456"})
    assert resp.status_code == 429


def test_pair_confirm_success_resets_fail_count(monkeypatch, tmp_path):
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    client.post('/pair/confirm', json={"pin": "wrong"})
    assert portal._pair_fail_count == 1
    resp = client.post('/pair/confirm', json={"pin": "123456"})
    assert resp.status_code == 200
    assert portal._pair_fail_count == 0
