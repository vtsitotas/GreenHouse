import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import check_leaked_secrets as leak


def _write(tmp_path, monkeypatch, device=None, hivemq=None, cam_token=None):
    if device is not None:
        p = tmp_path / 'device.json'
        p.write_text(json.dumps(device))
        monkeypatch.setattr(leak, 'DEVICE_CFG', str(p))
    else:
        monkeypatch.setattr(leak, 'DEVICE_CFG', str(tmp_path / 'absent.json'))
    if hivemq is not None:
        p = tmp_path / 'hivemq.json'
        p.write_text(json.dumps(hivemq))
        monkeypatch.setattr(leak, 'HIVEMQ_CFG', str(p))
    else:
        monkeypatch.setattr(leak, 'HIVEMQ_CFG', str(tmp_path / 'absent2.json'))
    if cam_token is not None:
        p = tmp_path / 'cam_token.txt'
        p.write_text(cam_token)
        monkeypatch.setattr(leak, 'CAM_TOKEN_FILE', str(p))
    else:
        monkeypatch.setattr(leak, 'CAM_TOKEN_FILE', str(tmp_path / 'absent.txt'))


def test_clean_unit_passes(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           device={'password': 'FreshRandomPw123456', 'api_token': 'x' * 32,
                   'pair_pin': '481920', 'bridge_password': 'AnotherFreshPw12345'},
           hivemq={'password': 'rotated-hivemq-pw'},
           cam_token='a-real-per-unit-token-value')
    assert leak.main() == 0


def test_detects_a_credential_whose_hash_is_on_the_leaked_list(tmp_path, monkeypatch):
    """Uses a synthetic value registered as leaked, so the real leaked literals
    stay out of this repo (see the module docstring on why that matters)."""
    victim = 'synthetic-leaked-value'
    monkeypatch.setitem(leak.KNOWN_LEAKED, leak._h(victim),
                        'commit c0383b3 — test fixture')
    _write(tmp_path, monkeypatch, device={'password': victim})
    problems = leak.find_problems(leak.collect_live_secrets())
    assert len(problems) == 1
    assert 'LEAKED' in problems[0][2]
    assert 'c0383b3' in problems[0][2]
    assert leak.main() == 1


def test_detects_a_leaked_value_in_the_hivemq_config_too(tmp_path, monkeypatch):
    victim = 'another-synthetic-leak'
    monkeypatch.setitem(leak.KNOWN_LEAKED, leak._h(victim), 'commit deadbee — fixture')
    _write(tmp_path, monkeypatch, hivemq={'password': victim})
    assert leak.main() == 1


def test_detects_the_public_cam_token_placeholder(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           device={'password': 'FreshRandomPw123456'},
           cam_token='your-cam-shared-token')
    problems = leak.find_problems(leak.collect_live_secrets())
    assert any('PLACEHOLDER' in why for _, _, why in problems)
    assert leak.main() == 1


def test_reports_every_compromised_value_not_just_the_first(tmp_path, monkeypatch):
    a, b = 'synthetic-a', 'synthetic-b'
    monkeypatch.setitem(leak.KNOWN_LEAKED, leak._h(a), 'fixture a')
    monkeypatch.setitem(leak.KNOWN_LEAKED, leak._h(b), 'fixture b')
    _write(tmp_path, monkeypatch,
           device={'password': a, 'bridge_password': b},
           cam_token='your-cam-shared-token')
    assert len(leak.find_problems(leak.collect_live_secrets())) == 3


def test_unprovisioned_unit_fails_rather_than_reporting_clean(tmp_path, monkeypatch):
    """No secrets at all must not look like 'nothing compromised'."""
    _write(tmp_path, monkeypatch)
    assert leak.main() == 1


def test_empty_values_are_ignored_not_treated_as_secrets(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           device={'password': 'FreshRandomPw123456', 'bridge_password': ''},
           cam_token='')
    live = leak.collect_live_secrets()
    assert 'device.json bridge_password' not in live
    assert 'cam_token.txt' not in live
    assert leak.main() == 0


def test_leaked_list_is_stored_as_hashes_not_plaintext():
    """The leaked literals were scrubbed out of git history on 2026-08-11
    (SECURITY.md §1). If this file carried them verbatim, the next commit would
    put them right back and silently undo that scrub."""
    assert len(leak.KNOWN_LEAKED) >= 2
    for digest in leak.KNOWN_LEAKED:
        assert len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
    # Each entry must still say where it leaked from. Commit hashes are no
    # longer usable for that — the scrub rewrote every one of them — so the
    # provenance is the file the credential sat in.
    assert all('bridge_esp32.ino' in why for why in leak.KNOWN_LEAKED.values())


def test_placeholder_detection_still_works_end_to_end(tmp_path, monkeypatch):
    """The public placeholders are safe to name, and prove the hashing path
    actually matches real values rather than only synthetic fixtures."""
    _write(tmp_path, monkeypatch,
           device={'password': 'FreshRandomPw123456'},
           cam_token='your-cam-shared-token')
    problems = leak.find_problems(leak.collect_live_secrets())
    assert any('PLACEHOLDER' in why for _, _, why in problems)
