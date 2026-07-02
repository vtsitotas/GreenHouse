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
