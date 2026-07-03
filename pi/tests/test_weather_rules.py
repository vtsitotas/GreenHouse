import os
import sys
import tempfile

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
