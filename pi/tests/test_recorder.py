# pi/tests/test_recorder.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import recorder


def test_buffer_averages_multiple_readings_in_same_minute():
    buf = recorder.MinuteBucketBuffer()
    buf.add(('zone', 'zone1', 'air_temperature'), 1000, 20.0)
    buf.add(('zone', 'zone1', 'air_temperature'), 1005, 22.0)
    buf.add(('zone', 'zone1', 'air_temperature'), 1010, 24.0)
    ready = buf.flush_ready(now=1060)  # minute (960-1020) has fully elapsed
    assert len(ready) == 1
    series_key, minute_ts, avg, mn, mx, n = ready[0]
    assert series_key == ('zone', 'zone1', 'air_temperature')
    assert minute_ts == 960
    assert avg == 22.0
    assert mn == 20.0
    assert mx == 24.0
    assert n == 3


def test_buffer_does_not_flush_current_incomplete_minute():
    buf = recorder.MinuteBucketBuffer()
    buf.add(('weather', None, 'temperature'), 1000, 15.0)
    ready = buf.flush_ready(now=1010)  # minute (960-1020) hasn't elapsed yet
    assert ready == []


def test_buffer_flush_ready_removes_flushed_buckets():
    buf = recorder.MinuteBucketBuffer()
    buf.add(('weather', None, 'temperature'), 1000, 15.0)
    buf.flush_ready(now=1060)
    assert buf.flush_ready(now=2000) == []  # already flushed, nothing left


def test_buffer_keeps_separate_series_separate():
    buf = recorder.MinuteBucketBuffer()
    buf.add(('zone', 'zone1', 'air_temperature'), 1000, 20.0)
    buf.add(('zone', 'zone2', 'air_temperature'), 1000, 30.0)
    ready = buf.flush_ready(now=1060)
    assert len(ready) == 2
    by_key = {r[0]: r[2] for r in ready}
    assert by_key[('zone', 'zone1', 'air_temperature')] == 20.0
    assert by_key[('zone', 'zone2', 'air_temperature')] == 30.0


def test_flush_all_returns_incomplete_buckets():
    buf = recorder.MinuteBucketBuffer()
    buf.add(('weather', None, 'temperature'), 1000, 15.0)
    ready = buf.flush_all()
    assert len(ready) == 1
    assert ready[0][2] == 15.0
    assert buf.flush_all() == []  # drained


import tempfile


def test_init_db_creates_schema():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'series', 'readings', 'readings_hourly', 'meta'} <= tables
        conn.close()


def test_get_or_create_series_id_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        id1 = recorder.get_or_create_series_id(conn, 'zone', 'zone1', 'air_temperature')
        id2 = recorder.get_or_create_series_id(conn, 'zone', 'zone1', 'air_temperature')
        assert id1 == id2
        conn.close()


def test_get_or_create_series_id_handles_null_zone():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        id1 = recorder.get_or_create_series_id(conn, 'weather', None, 'temperature')
        id2 = recorder.get_or_create_series_id(conn, 'weather', None, 'temperature')
        assert id1 == id2
        row = conn.execute('SELECT zone FROM series WHERE id=?', (id1,)).fetchone()
        assert row[0] is None
        conn.close()


def test_write_buckets_inserts_rows():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        buckets = [
            (('zone', 'zone1', 'air_temperature'), 960, 22.0, 20.0, 24.0, 3),
            (('weather', None, 'temperature'), 960, 15.0, 15.0, 15.0, 1),
        ]
        recorder.write_buckets(conn, series_ids, buckets)
        rows = conn.execute('SELECT COUNT(*) FROM readings').fetchone()
        assert rows[0] == 2
        conn.close()


def test_write_buckets_replaces_on_duplicate_series_and_ts():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'soil_moisture'), 960, 40.0, 40.0, 40.0, 1),
        ])
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'soil_moisture'), 960, 45.0, 45.0, 45.0, 2),
        ])
        rows = conn.execute('SELECT avg, n FROM readings').fetchall()
        assert len(rows) == 1
        assert rows[0] == (45.0, 2)
        conn.close()


def test_write_buckets_handles_empty_list():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        recorder.write_buckets(conn, {}, [])  # must not raise
        conn.close()
