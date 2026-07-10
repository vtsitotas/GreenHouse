import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import weather
import recorder


def test_duration_coverage_fires_when_all_buckets_below_threshold():
    fires, coverage = weather.duration_coverage(
        values=[25.0, 24.0, 26.0, 23.0], op='<', threshold=30.0, expected_buckets=4)
    assert fires is True
    assert coverage == 1.0


def test_duration_coverage_does_not_fire_if_one_bucket_fails_condition():
    fires, coverage = weather.duration_coverage(
        values=[25.0, 35.0, 26.0, 23.0], op='<', threshold=30.0, expected_buckets=4)
    assert fires is False


def test_duration_coverage_does_not_fire_below_80_percent_coverage():
    # only 2 of 10 expected minute-buckets present — data gap, not a real signal
    fires, coverage = weather.duration_coverage(
        values=[10.0, 12.0], op='<', threshold=30.0, expected_buckets=10)
    assert fires is False
    assert coverage == 0.2


def test_duration_coverage_fires_at_exactly_80_percent():
    values = [10.0] * 8
    fires, coverage = weather.duration_coverage(
        values=values, op='<', threshold=30.0, expected_buckets=10)
    assert fires is True
    assert coverage == 0.8


def test_duration_coverage_handles_empty_values():
    fires, coverage = weather.duration_coverage(
        values=[], op='<', threshold=30.0, expected_buckets=10)
    assert fires is False
    assert coverage == 0.0


def test_eval_duration_rule_queries_zone_series():
    # 5-minute window → 5 expected minute-buckets. Seed 4 of them (80%
    # coverage, exactly at the threshold), all satisfying < 30.0.
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        now = 100000
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'soil_moisture'), now - 240, 25.0, 25.0, 25.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 180, 26.0, 26.0, 26.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 120, 25.0, 25.0, 25.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 60, 27.0, 27.0, 27.0, 1),
        ])
        fired = weather.eval_duration_rule(
            conn, zone='zone1', metric='soil_moisture', op='<', threshold=30.0,
            duration_minutes=5, now=now)
        assert fired is True
        conn.close()


def test_eval_duration_rule_returns_false_for_unknown_series():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        fired = weather.eval_duration_rule(
            conn, zone='zoneNope', metric='soil_moisture', op='<', threshold=30.0,
            duration_minutes=5, now=100000)
        assert fired is False
        conn.close()


def test_eval_duration_rule_does_not_fire_on_sparse_startup_data():
    # Regression test: right after the recorder (re)starts, only a handful
    # of minute-buckets exist yet, even though the rule asks for a long
    # sustained window. expected_buckets must be derived from
    # duration_minutes (60), not from the observed data span — otherwise
    # coverage against the tiny observed span would read as 100% and the
    # rule would fire after only 3 minutes instead of the requested 60.
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        now = 100000
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'soil_moisture'), now - 120, 25.0, 25.0, 25.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 60, 26.0, 26.0, 26.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now, 27.0, 27.0, 27.0, 1),
        ])
        fired = weather.eval_duration_rule(
            conn, zone='zone1', metric='soil_moisture', op='<', threshold=30.0,
            duration_minutes=60, now=now)
        assert fired is False  # coverage = 3/60 = 5%, far below the 80% guard
        conn.close()


def test_eval_rules_live_metric_sends_push_on_fire(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    rule = {
        'id': 'r1', 'name': 'High Temp', 'enabled': True,
        'trigger': {'metric': 'temperature', 'op': '>', 'value': 30.0},
        'action': {'actuator': 'fan', 'command': 'on'},
    }
    weather.eval_rules([rule], {'temperature': 35.0})

    assert len(pushed) == 1
    title, body = pushed[0]
    assert title == 'High Temp'
    assert 'High Temp' in body


def test_eval_rules_does_not_push_when_rule_does_not_fire(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    rule = {
        'id': 'r1', 'name': 'High Temp', 'enabled': True,
        'trigger': {'metric': 'temperature', 'op': '>', 'value': 30.0},
        'action': {'actuator': 'fan', 'command': 'on'},
    }
    weather.eval_rules([rule], {'temperature': 20.0})

    assert pushed == []


def test_maybe_send_daily_summary_sends_push(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    monkeypatch.setattr(weather, '_last_summary_date', None)

    class _FrozenClock:
        @staticmethod
        def now():
            return datetime(2026, 7, 10, 7, 0, 0)
    monkeypatch.setattr(weather, 'datetime', _FrozenClock)

    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    data = {'hourly': {
        'temperature_2m': [20.0] * 24,
        'precipitation': [0.0] * 24,
    }}
    weather.maybe_send_daily_summary(data, {'temperature': 22.0, 'wind_kmh': 5.0})

    assert len(pushed) == 1
    assert pushed[0][0] == "Today's forecast"


def test_maybe_send_frost_alert_sends_push(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    monkeypatch.setattr(weather, '_last_frost_alert', None)
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    data = {'hourly': {'temperature_2m': [-2.0] * 12}}
    weather.maybe_send_frost_alert(data)

    assert len(pushed) == 1
    assert pushed[0][0] == 'Frost warning'


def test_pull_rules_from_mqtt_writes_valid_payload(monkeypatch, tmp_path):
    rules_file = tmp_path / 'rules.json'
    rules_file.write_text('[]')
    monkeypatch.setattr(weather, 'RULES_CFG', str(rules_file))

    new_rules = '[{"id":"r1","name":"Test","enabled":true,"trigger":{"metric":"temperature","op":">","value":30.0}}]'
    fake_result = MagicMock()
    fake_result.stdout = new_rules
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: fake_result)

    reload_flag = tmp_path / 'reload'
    monkeypatch.setattr(weather, 'RELOAD_FLAG', str(reload_flag))

    weather._pull_rules_from_mqtt()

    assert rules_file.read_text() == new_rules
    assert reload_flag.exists()


def test_pull_rules_from_mqtt_ignores_empty_or_invalid_payload(monkeypatch, tmp_path):
    rules_file = tmp_path / 'rules.json'
    rules_file.write_text('[]')
    monkeypatch.setattr(weather, 'RULES_CFG', str(rules_file))

    fake_result = MagicMock()
    fake_result.stdout = 'not valid json'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: fake_result)

    weather._pull_rules_from_mqtt()

    assert rules_file.read_text() == '[]'  # unchanged — invalid payload ignored


def test_pull_rules_from_mqtt_noop_on_no_message(monkeypatch, tmp_path):
    rules_file = tmp_path / 'rules.json'
    rules_file.write_text('[]')
    monkeypatch.setattr(weather, 'RULES_CFG', str(rules_file))

    fake_result = MagicMock()
    fake_result.stdout = ''
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: fake_result)

    weather._pull_rules_from_mqtt()

    assert rules_file.read_text() == '[]'


def test_eval_rules_alert_only_rule_does_not_publish_actuator_command(monkeypatch):
    published = []
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: published.append(a))
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    rule = {
        'id': 'zone1-dry', 'name': 'Zone 1 soil dry', 'enabled': True,
        'trigger': {'metric': 'zone1/soil_moisture', 'op': '<', 'value': 15.0, 'duration_minutes': 5},
    }
    # duration rules need the recorder DB; use a real one seeded to fire.
    # eval_rules() computes `now` internally as int(time.time()) (it takes
    # no `now` parameter, unlike eval_duration_rule's own direct tests
    # elsewhere in this file) — seed data relative to real current time,
    # not a fixed fake epoch, or the cutoff filter will never match it.
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        now = int(time.time())
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'soil_moisture'), now - 240, 10.0, 10.0, 10.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 180, 10.0, 10.0, 10.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 120, 10.0, 10.0, 10.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 60,  10.0, 10.0, 10.0, 1),
        ])
        conn.close()
        monkeypatch.setattr(weather, 'RECORDER_DB', os.path.join(d, 'test.db'))
        conn2 = sqlite3.connect(os.path.join(d, 'test.db'))
        monkeypatch.setattr(weather.sqlite3, 'connect', lambda *a, **k: conn2)
        weather.eval_rules([rule], {})

    assert len(pushed) == 1
    assert pushed[0][0] == 'Zone 1 soil dry'
    # No actuator command published — only the mqtt alert, no `greenhouse/actuators/...` topic
    actuator_topics = [p[0] for p in published if p and str(p[0]).startswith('greenhouse/actuators/')]
    assert actuator_topics == []


def test_eval_rules_notify_false_skips_push_but_still_publishes_alert(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    rule = {
        'id': 'r1', 'name': 'Silent Rule', 'enabled': True, 'notify': False,
        'trigger': {'metric': 'temperature', 'op': '>', 'value': 30.0},
        'action': {'actuator': 'fan1', 'command': 'ON'},
    }
    weather.eval_rules([rule], {'temperature': 35.0})

    assert pushed == []


def test_load_notification_settings_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, 'NOTIFICATION_SETTINGS_CFG', str(tmp_path / 'missing.json'))
    settings = weather.load_notification_settings()
    assert settings == {'frost_forecast': True, 'daily_summary': True}


def test_load_notification_settings_reads_file(tmp_path, monkeypatch):
    cfg = tmp_path / 'notification_settings.json'
    cfg.write_text('{"frost_forecast": false, "daily_summary": true}')
    monkeypatch.setattr(weather, 'NOTIFICATION_SETTINGS_CFG', str(cfg))
    settings = weather.load_notification_settings()
    assert settings == {'frost_forecast': False, 'daily_summary': True}


def test_maybe_send_frost_alert_respects_frost_forecast_off(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    monkeypatch.setattr(weather, '_last_frost_alert', None)
    monkeypatch.setattr(weather, 'load_notification_settings',
                         lambda: {'frost_forecast': False, 'daily_summary': True})
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    data = {'hourly': {'temperature_2m': [-2.0] * 12}}
    weather.maybe_send_frost_alert(data)

    assert pushed == []  # push suppressed
    # mqtt alert still fires — verified indirectly: _last_frost_alert still gets set
    assert weather._last_frost_alert is not None


def test_maybe_send_daily_summary_respects_daily_summary_off(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    monkeypatch.setattr(weather, '_last_summary_date', None)
    monkeypatch.setattr(weather, 'load_notification_settings',
                         lambda: {'frost_forecast': True, 'daily_summary': False})

    class _FrozenClock:
        @staticmethod
        def now():
            return datetime(2026, 7, 10, 7, 0, 0)
    monkeypatch.setattr(weather, 'datetime', _FrozenClock)

    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    data = {'hourly': {'temperature_2m': [20.0] * 24, 'precipitation': [0.0] * 24}}
    weather.maybe_send_daily_summary(data, {'temperature': 22.0, 'wind_kmh': 5.0})

    assert pushed == []
    assert weather._last_summary_date is not None
