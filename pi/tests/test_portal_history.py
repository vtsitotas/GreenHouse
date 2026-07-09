import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'portal'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import portal
import recorder


def _seed_db(db_path, now):
    conn = recorder.init_db(db_path)
    series_ids = {}
    recorder.write_buckets(conn, series_ids, [
        (('zone', 'zone1', 'air_temperature'), now - 120, 21.0, 20.0, 22.0, 2),
        (('zone', 'zone1', 'air_temperature'), now - 60, 23.0, 22.0, 24.0, 2),
        (('weather', None, 'temperature'), now - 60, 18.0, 18.0, 18.0, 1),
    ])
    conn.close()


def test_history_series_lists_known_series(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    _seed_db(db_path, now=100000)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    client = portal.app.test_client()
    resp = client.get('/api/history/series')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    kinds_zones_metrics = {(d['kind'], d['zone'], d['metric']) for d in data}
    assert ('zone', 'zone1', 'air_temperature') in kinds_zones_metrics
    assert ('weather', None, 'temperature') in kinds_zones_metrics


def test_history_returns_points_for_known_series(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    now = 100000
    _seed_db(db_path, now=now)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    monkeypatch.setattr(portal.time, 'time', lambda: now)
    client = portal.app.test_client()
    resp = client.get('/api/history?zone=zone1&metric=air_temperature&hours=1')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['zone'] == 'zone1'
    assert data['metric'] == 'air_temperature'
    assert data['resolution'] == 'minute'
    assert len(data['points']) == 2


def test_history_returns_empty_points_for_unknown_series(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    _seed_db(db_path, now=100000)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    client = portal.app.test_client()
    resp = client.get('/api/history?zone=zoneNope&metric=air_temperature&hours=1')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['points'] == []


def test_history_requires_metric_param(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    _seed_db(db_path, now=100000)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    client = portal.app.test_client()
    resp = client.get('/api/history?zone=zone1')
    assert resp.status_code == 400


def test_history_selects_hourly_table_beyond_48h(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    now = 100000
    _seed_db(db_path, now=now)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    monkeypatch.setattr(portal.time, 'time', lambda: now)
    client = portal.app.test_client()
    resp = client.get('/api/history?zone=zone1&metric=air_temperature&hours=72')
    data = json.loads(resp.data)
    assert data['resolution'] == 'hour'


def test_history_accepts_since_until_and_bounds_points(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    now = 100000
    _seed_db(db_path, now=now)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    client = portal.app.test_client()
    resp = client.get(
        f'/api/history?zone=zone1&metric=air_temperature&since={now - 90}&until={now - 30}')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['resolution'] == 'minute'
    assert len(data['points']) == 1
    assert data['points'][0][0] == now - 60


def test_history_since_until_overrides_hours(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    now = 100000
    _seed_db(db_path, now=now)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    client = portal.app.test_client()
    # hours=1 alone would select minute resolution; a >48h since/until span
    # must win when both are given.
    resp = client.get(
        '/api/history?zone=zone1&metric=air_temperature&hours=1'
        f'&since={now - 100 * 3600}&until={now}')
    data = json.loads(resp.data)
    assert data['resolution'] == 'hour'


def test_history_requires_since_and_until_together(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    _seed_db(db_path, now=100000)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    client = portal.app.test_client()
    resp = client.get('/api/history?zone=zone1&metric=air_temperature&since=100')
    assert resp.status_code == 400
