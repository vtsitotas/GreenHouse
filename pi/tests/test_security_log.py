import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import security_log


def _fresh(tmp_path, monkeypatch):
    log = tmp_path / 'sec.log'
    monkeypatch.setattr(security_log, 'SECURITY_LOG', str(log))
    security_log._last_alert.clear()
    return log


def test_event_is_written_as_structured_json(tmp_path, monkeypatch):
    log = _fresh(tmp_path, monkeypatch)
    security_log.log_security_event('pair_pin_failure', 'wrong PIN', source='10.0.0.5')
    record = json.loads(log.read_text().strip())
    assert record['kind'] == 'pair_pin_failure'
    assert record['detail'] == 'wrong PIN'
    assert record['source'] == '10.0.0.5'
    assert isinstance(record['ts'], int)


def test_events_append_rather_than_overwrite(tmp_path, monkeypatch):
    log = _fresh(tmp_path, monkeypatch)
    for i in range(3):
        security_log.log_security_event('cam_auth_failure', f'attempt {i}')
    assert len(log.read_text().strip().splitlines()) == 3


def test_logging_never_raises_when_the_file_is_unwritable(tmp_path, monkeypatch, capsys):
    """An auth path calls this. If it could throw, a full disk would become a
    way to break authentication."""
    monkeypatch.setattr(security_log, 'SECURITY_LOG', str(tmp_path / 'no' / 'such' / 'dir.log'))
    security_log._last_alert.clear()
    record = security_log.log_security_event('cam_auth_failure', 'x')
    assert record['kind'] == 'cam_auth_failure'
    assert 'log write failed' in capsys.readouterr().out


def test_alertable_event_notifies_the_owner(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setitem(sys.modules, 'push',
                        type(sys)('push'))
    sys.modules['push'].send_push = lambda t, b: sent.append((t, b))
    security_log.log_security_event('pair_lockout', '5 failed attempts', source='10.0.0.9')
    assert len(sent) == 1
    assert 'pair lockout' in sent[0][1]
    assert '10.0.0.9' in sent[0][1]


def test_routine_event_does_not_notify(tmp_path, monkeypatch):
    """A single wrong PIN is a typo. Alerting on it trains the owner to ignore
    the channel."""
    _fresh(tmp_path, monkeypatch)
    sent = []
    sys.modules['push'] = type(sys)('push')
    sys.modules['push'].send_push = lambda t, b: sent.append((t, b))
    security_log.log_security_event('pair_pin_failure', 'wrong PIN')
    assert sent == []


def test_alert_flooding_is_rate_limited(tmp_path, monkeypatch):
    """Without this the notification channel is a weapon: an attacker triggers
    endless failures until the owner mutes greenhouse alerts entirely, and then
    misses the real one."""
    _fresh(tmp_path, monkeypatch)
    sent = []
    sys.modules['push'] = type(sys)('push')
    sys.modules['push'].send_push = lambda t, b: sent.append((t, b))
    for _ in range(50):
        security_log.log_security_event('cam_auth_failure', 'bad token')
    assert len(sent) == 1                      # one push, not fifty
    assert len(security_log.read_recent(path=str(tmp_path / 'sec.log'))) == 50  # all logged


def test_cooldown_expires_so_a_later_attack_still_alerts(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    sent = []
    sys.modules['push'] = type(sys)('push')
    sys.modules['push'].send_push = lambda t, b: sent.append((t, b))
    security_log.log_security_event('cam_auth_failure', 'first')
    # Fast-forward past the cooldown.
    for k in security_log._last_alert:
        security_log._last_alert[k] -= security_log.ALERT_COOLDOWN_SECONDS + 1
    security_log.log_security_event('cam_auth_failure', 'later')
    assert len(sent) == 2


def test_different_event_kinds_have_independent_cooldowns(tmp_path, monkeypatch):
    """A flood of one kind must not mask a different, possibly worse, kind."""
    _fresh(tmp_path, monkeypatch)
    sent = []
    sys.modules['push'] = type(sys)('push')
    sys.modules['push'].send_push = lambda t, b: sent.append((t, b))
    security_log.log_security_event('cam_auth_failure', 'x')
    security_log.log_security_event('pair_lockout', 'y')
    assert len(sent) == 2


def test_push_failure_does_not_break_the_caller(tmp_path, monkeypatch, capsys):
    _fresh(tmp_path, monkeypatch)
    def boom(*_a):
        raise RuntimeError('no firebase')
    sys.modules['push'] = type(sys)('push')
    sys.modules['push'].send_push = boom
    security_log.log_security_event('pair_lockout', 'x')   # must not raise
    assert 'alert push failed' in capsys.readouterr().out


def test_read_recent_and_summarize(tmp_path, monkeypatch):
    log = _fresh(tmp_path, monkeypatch)
    sys.modules['push'] = type(sys)('push')
    sys.modules['push'].send_push = lambda *_a: None
    security_log.log_security_event('cam_auth_failure', 'a')
    security_log.log_security_event('cam_auth_failure', 'b')
    security_log.log_security_event('pair_pin_failure', 'c')
    events = security_log.read_recent(path=str(log))
    assert len(events) == 3
    assert security_log.summarize(events) == {'cam_auth_failure': 2, 'pair_pin_failure': 1}


def test_read_recent_tolerates_a_corrupt_line(tmp_path, monkeypatch):
    log = _fresh(tmp_path, monkeypatch)
    log.write_text('{"kind":"ok"}\nnot-json\n{"kind":"also_ok"}\n')
    assert len(security_log.read_recent(path=str(log))) == 2


def test_read_recent_on_missing_file_returns_empty(tmp_path, monkeypatch):
    assert security_log.read_recent(path=str(tmp_path / 'absent.log')) == []
