import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'portal'))
import portal


def _reset_state():
    portal._pair_failures.clear()
    portal._global_pair_failures[0] = 0
    portal._global_pair_failures[1] = 0.0


@pytest.fixture(autouse=True)
def _reset_pair_state(monkeypatch):
    _reset_state()
    # The 1s throttle in pair_confirm() is real production behavior we don't
    # need to pay for in every test run.
    monkeypatch.setattr(portal.time, 'sleep', lambda *_a, **_kw: None)
    yield
    _reset_state()


def _failures_for(ip='127.0.0.1'):
    entry = portal._pair_failures.get(ip)
    return 0 if entry is None else entry[0]


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
    assert _failures_for() == 1


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
    assert _failures_for() == 1
    resp = client.post('/pair/confirm', json={"pin": "123456"})
    assert resp.status_code == 200
    assert _failures_for() == 0


# ── Portal hardening: headers, body limits, error hygiene ────────────────────

def test_security_headers_present_on_responses(monkeypatch):
    monkeypatch.setattr(portal, '_load_config', lambda: {'api_token': 'tok'})
    client = portal.app.test_client()
    resp = client.get('/api/history/series?token=tok')
    assert resp.headers['X-Content-Type-Options'] == 'nosniff'
    assert resp.headers['X-Frame-Options'] == 'DENY'
    assert resp.headers['Referrer-Policy'] == 'no-referrer'
    assert "frame-ancestors 'none'" in resp.headers['Content-Security-Policy']
    assert "base-uri 'none'" in resp.headers['Content-Security-Policy']


def test_csp_nonce_matches_the_inline_script_tag(monkeypatch):
    """The WiFi page's inline script must carry the same nonce the CSP allows,
    or the captive-portal network picker silently stops working."""
    monkeypatch.setattr(portal, '_ap_mode', lambda: True)
    client = portal.app.test_client()
    resp = client.get('/')
    csp = resp.headers['Content-Security-Policy']
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert nonce
    assert f'<script nonce="{nonce}">'.encode() in resp.data
    # ...and it must be fresh per response, not a fixed constant.
    other = client.get('/')
    assert other.headers['Content-Security-Policy'] != csp


def test_csp_blocks_generic_inline_script(monkeypatch):
    monkeypatch.setattr(portal, '_ap_mode', lambda: True)
    client = portal.app.test_client()
    csp = client.get('/').headers['Content-Security-Policy']
    assert "'unsafe-inline'" not in csp.split('script-src')[1].split(';')[0]


def test_oversized_request_body_is_rejected(monkeypatch):
    client = portal.app.test_client()
    resp = client.post('/pair/confirm', data=b'x' * (128 * 1024),
                       content_type='application/json')
    assert resp.status_code == 413


def test_pair_confirm_uses_constant_time_compare(monkeypatch):
    """Guards against a future refactor reintroducing a short-circuiting `!=`,
    which would leak the PIN prefix-by-prefix through response timing."""
    import inspect
    src = inspect.getsource(portal.pair_confirm)
    assert 'compare_digest' in src
    assert '!= expected_pin' not in src


def test_pair_confirm_error_does_not_leak_internals(monkeypatch):
    def boom():
        raise RuntimeError('/etc/greenhouse/device.json is missing')
    monkeypatch.setattr(portal, '_load_config', boom)
    client = portal.app.test_client()
    resp = client.post('/pair/confirm', json={'pin': '123456'})
    assert resp.status_code == 500
    assert b'/etc/greenhouse' not in resp.data


# ── Per-IP pairing lockout (the old global counter was a DoS vector) ─────────
# Previously 5 wrong PINs from ANY source latched a global flag that only a
# service restart cleared — so any device on the network could permanently
# block the owner from pairing, and on a WiFi-less unit "restart" means
# physically power-cycling the Pi.

def _post_pin(client, pin, ip):
    return client.post('/pair/confirm', json={'pin': pin},
                       environ_overrides={'REMOTE_ADDR': ip})


def test_attacker_lockout_does_not_block_a_different_device(monkeypatch, tmp_path):
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    for _ in range(portal.MAX_PAIR_ATTEMPTS):
        assert _post_pin(client, '000000', '10.0.0.66').status_code == 401
    # Attacker is now locked out...
    assert _post_pin(client, '123456', '10.0.0.66').status_code == 429
    # ...but the owner, on another device, can still pair right now.
    assert _post_pin(client, '123456', '192.168.1.20').status_code == 200


def test_lockout_response_tells_the_client_when_to_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    for _ in range(portal.MAX_PAIR_ATTEMPTS):
        _post_pin(client, '000000', '10.0.0.66')
    resp = _post_pin(client, '000000', '10.0.0.66')
    assert resp.status_code == 429
    assert json.loads(resp.data)['retry_after_seconds'] > 0


def test_lockout_expires_after_the_window(monkeypatch, tmp_path):
    """Unlike the old latch-until-restart behavior, a lockout self-heals."""
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    for _ in range(portal.MAX_PAIR_ATTEMPTS):
        _post_pin(client, '000000', '10.0.0.66')
    assert _post_pin(client, '123456', '10.0.0.66').status_code == 429
    # Fast-forward past the lockout window.
    entry = portal._pair_failures['10.0.0.66']
    entry[1] -= portal.PAIR_LOCKOUT_SECONDS + 1
    assert _post_pin(client, '123456', '10.0.0.66').status_code == 200


def test_forged_x_forwarded_for_cannot_reset_the_per_ip_limit(monkeypatch, tmp_path):
    """No proxy fronts this service, so XFF must not be honoured — otherwise a
    caller forges a new identity per request and the limit means nothing."""
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    for i in range(portal.MAX_PAIR_ATTEMPTS):
        client.post('/pair/confirm', json={'pin': '000000'},
                    headers={'X-Forwarded-For': f'10.1.1.{i}'},
                    environ_overrides={'REMOTE_ADDR': '10.0.0.66'})
    resp = client.post('/pair/confirm', json={'pin': '123456'},
                       headers={'X-Forwarded-For': '10.1.1.99'},
                       environ_overrides={'REMOTE_ADDR': '10.0.0.66'})
    assert resp.status_code == 429


def test_global_backstop_still_exists_for_distributed_attempts(monkeypatch, tmp_path):
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    # Spread failures across many source IPs so no per-IP limit ever trips.
    for i in range(portal.MAX_GLOBAL_PAIR_FAILURES):
        _post_pin(client, '000000', f'10.2.{i // 250}.{i % 250}')
    assert _post_pin(client, '123456', '10.9.9.9').status_code == 429


def test_failure_table_is_bounded(monkeypatch, tmp_path):
    """A source-cycling attacker must not be able to grow memory without limit."""
    monkeypatch.setattr(portal, '_CONFIG', _write_config(tmp_path))
    monkeypatch.setattr(portal, '_load_hivemq', lambda: {})
    client = portal.app.test_client()
    for i in range(400):
        _post_pin(client, '000000', f'10.5.{i // 250}.{i % 250}')
    assert len(portal._pair_failures) <= 256


# ── HTTPS transport + the require_https enforcement switch ───────────────────
# Plaintext HTTP on :80 must keep working for the captive portal (the OS popup
# won't fire otherwise), but the endpoints carrying secrets can be forced onto
# TLS once every paired phone speaks it.

def test_https_enforcement_blocks_plaintext_sensitive_endpoints(monkeypatch, tmp_path):
    flag = tmp_path / 'require_https'
    flag.write_text('')
    monkeypatch.setattr(portal, 'REQUIRE_HTTPS_FLAG', str(flag))
    monkeypatch.setattr(portal, '_load_config', lambda: {'api_token': 'tok'})
    client = portal.app.test_client()
    assert client.get('/api/history?zone=z&metric=m').status_code == 403
    assert client.post('/pair/confirm', json={'pin': '123456'}).status_code == 403


def test_https_enforcement_keeps_the_captive_portal_reachable(monkeypatch, tmp_path):
    """Enforcing HTTPS must never break first-boot WiFi setup: the setup page
    and the no-secrets existence check stay on plaintext by design."""
    flag = tmp_path / 'require_https'
    flag.write_text('')
    monkeypatch.setattr(portal, 'REQUIRE_HTTPS_FLAG', str(flag))
    monkeypatch.setattr(portal, '_ap_mode', lambda: True)
    client = portal.app.test_client()
    assert client.get('/').status_code == 200
    assert client.get('/pair').status_code == 200


def test_no_enforcement_by_default(monkeypatch, tmp_path):
    """Shipping with enforcement on would lock out any phone not yet updated,
    so the flag must be opt-in."""
    monkeypatch.setattr(portal, 'REQUIRE_HTTPS_FLAG', str(tmp_path / 'absent'))
    monkeypatch.setattr(portal, '_load_config', lambda: {'api_token': 'tok'})
    client = portal.app.test_client()
    # 401 (auth), not 403 (transport) — the request was allowed through.
    assert client.get('/api/history?zone=z&metric=m').status_code == 401


def test_https_disabled_gracefully_when_certs_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(portal, 'PORTAL_CERT', str(tmp_path / 'nope.crt'))
    monkeypatch.setattr(portal, 'PORTAL_KEY', str(tmp_path / 'nope.key'))
    assert portal._https_available() is False


def test_https_available_when_both_cert_and_key_exist(monkeypatch, tmp_path):
    crt, key = tmp_path / 'portal.crt', tmp_path / 'portal.key'
    crt.write_text('x')
    key.write_text('y')
    monkeypatch.setattr(portal, 'PORTAL_CERT', str(crt))
    monkeypatch.setattr(portal, 'PORTAL_KEY', str(key))
    assert portal._https_available() is True
