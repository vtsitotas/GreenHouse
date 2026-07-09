import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import recorder
import history_query


def _seed_db(db_path, now):
    conn = recorder.init_db(db_path)
    series_ids = {}
    recorder.write_buckets(conn, series_ids, [
        (('zone', 'zone1', 'air_temperature'), now - 120, 21.0, 20.0, 22.0, 2),
        (('zone', 'zone1', 'air_temperature'), now - 60, 23.0, 22.0, 24.0, 2),
        (('weather', None, 'temperature'), now - 60, 18.0, 18.0, 18.0, 1),
    ])
    return conn


def test_hours_mode_returns_points_for_known_series(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, 'test.db')
        now = 100000
        conn = _seed_db(db_path, now)
        monkeypatch.setattr(history_query.time, 'time', lambda: now)
        result = history_query.query_points(conn, 'zone', 'zone1', 'air_temperature', hours=1)
        assert result['resolution'] == 'minute'
        assert len(result['points']) == 2
        conn.close()


def test_hours_mode_selects_hourly_table_beyond_48h(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, 'test.db')
        now = 100000
        conn = _seed_db(db_path, now)
        monkeypatch.setattr(history_query.time, 'time', lambda: now)
        result = history_query.query_points(conn, 'zone', 'zone1', 'air_temperature', hours=72)
        assert result['resolution'] == 'hour'
        conn.close()


def test_hours_mode_returns_empty_points_for_unknown_series(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, 'test.db')
        now = 100000
        conn = _seed_db(db_path, now)
        monkeypatch.setattr(history_query.time, 'time', lambda: now)
        result = history_query.query_points(conn, 'zone', 'zoneNope', 'air_temperature', hours=1)
        assert result == {'zone': 'zoneNope', 'metric': 'air_temperature',
                           'resolution': 'minute', 'points': []}
        conn.close()


def test_since_until_mode_bounds_points_to_the_given_window():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, 'test.db')
        now = 100000
        conn = _seed_db(db_path, now)
        # Only the (now-60) point falls inside [now-90, now-30]; (now-120) is excluded.
        result = history_query.query_points(
            conn, 'zone', 'zone1', 'air_temperature', since=now - 90, until=now - 30)
        assert result['resolution'] == 'minute'
        assert len(result['points']) == 1
        assert result['points'][0][0] == now - 60
        conn.close()


def test_since_until_mode_selects_hourly_table_for_spans_beyond_48h():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, 'test.db')
        now = 100000
        conn = _seed_db(db_path, now)
        result = history_query.query_points(
            conn, 'zone', 'zone1', 'air_temperature', since=now - 100 * 3600, until=now)
        assert result['resolution'] == 'hour'
        conn.close()


def test_since_until_overrides_hours_when_both_given():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, 'test.db')
        now = 100000
        conn = _seed_db(db_path, now)
        # hours=1 alone would select minute resolution; a >48h since/until
        # span must win when both are given.
        result = history_query.query_points(
            conn, 'zone', 'zone1', 'air_temperature',
            hours=1, since=now - 100 * 3600, until=now)
        assert result['resolution'] == 'hour'
        conn.close()


def test_since_without_until_raises_value_error():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, 'test.db')
        conn = _seed_db(db_path, now=100000)
        try:
            history_query.query_points(conn, 'zone', 'zone1', 'air_temperature', since=1000)
        except ValueError as e:
            assert 'since and until must be provided together' in str(e)
        else:
            raise AssertionError('expected ValueError')
        conn.close()


def test_until_without_since_raises_value_error():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, 'test.db')
        conn = _seed_db(db_path, now=100000)
        try:
            history_query.query_points(conn, 'zone', 'zone1', 'air_temperature', until=2000)
        except ValueError:
            pass
        else:
            raise AssertionError('expected ValueError')
        conn.close()
