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


def test_write_buckets_rolls_back_and_stays_usable_after_failure():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        # Second row violates the NOT NULL constraint on `avg`, so executemany
        # fails partway through the batch.
        bad_buckets = [
            (('zone', 'zone1', 'air_temperature'), 960, 22.0, 20.0, 24.0, 3),
            (('zone', 'zone1', 'air_temperature'), 1020, None, 20.0, 24.0, 3),
        ]
        raised = False
        try:
            recorder.write_buckets(conn, series_ids, bad_buckets)
        except sqlite3.IntegrityError:
            raised = True
        assert raised  # the failure must propagate, not be swallowed

        # A wedged connection would raise OperationalError here ("cannot
        # start a transaction within a transaction"). It must not.
        good_buckets = [
            (('zone', 'zone1', 'air_temperature'), 1080, 25.0, 25.0, 25.0, 1),
        ]
        recorder.write_buckets(conn, series_ids, good_buckets)  # must not raise

        rows = conn.execute('SELECT COUNT(*) FROM readings').fetchone()
        assert rows[0] == 1  # failed transaction fully rolled back; only the good write persisted
        conn.close()


def test_rollup_creates_hourly_row_from_minute_rows():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        # Two minute buckets inside the same completed hour (hour starts at 3600)
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'air_temperature'), 3600, 20.0, 20.0, 20.0, 2),
            (('zone', 'zone1', 'air_temperature'), 3660, 24.0, 22.0, 26.0, 2),
        ])
        now = 3600 + 3600 + 3600  # well past the hour's end, so it's "completed"
        recorder.rollup_and_prune(conn, now, raw_days=90, hourly_days=730)
        rows = conn.execute('SELECT ts, avg, min, max, n FROM readings_hourly').fetchall()
        assert len(rows) == 1
        ts, avg, mn, mx, n = rows[0]
        assert ts == 3600
        assert avg == 22.0  # (20*2 + 24*2) / 4
        assert mn == 20.0
        assert mx == 26.0
        assert n == 4
        conn.close()


def test_rollup_advances_watermark_and_does_not_reprocess():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        recorder.write_buckets(conn, series_ids, [
            (('weather', None, 'temperature'), 3600, 15.0, 15.0, 15.0, 1),
        ])
        now = 3600 * 3
        recorder.rollup_and_prune(conn, now, raw_days=90, hourly_days=730)
        recorder.write_buckets(conn, series_ids, [
            # A late/duplicate write into the already-rolled-up hour
            (('weather', None, 'temperature'), 3600, 99.0, 99.0, 99.0, 1),
        ])
        recorder.rollup_and_prune(conn, now, raw_days=90, hourly_days=730)
        rows = conn.execute('SELECT avg FROM readings_hourly').fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 15.0  # unchanged — watermark already passed this hour
        conn.close()


def test_rollup_prunes_old_raw_and_hourly_rows():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        one_day = 86400
        now = 200 * one_day
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'air_temperature'), now - 100 * one_day, 20.0, 20.0, 20.0, 1),
            (('zone', 'zone1', 'air_temperature'), now - 1 * one_day, 21.0, 21.0, 21.0, 1),
        ])
        conn.execute('BEGIN')
        conn.execute(
            "INSERT INTO readings_hourly (series_id, ts, avg, min, max, n) "
            "SELECT id, ?, 20.0, 20.0, 20.0, 1 FROM series LIMIT 1",
            (now - 800 * one_day,))
        conn.execute('COMMIT')
        recorder.rollup_and_prune(conn, now, raw_days=90, hourly_days=730)
        raw_count = conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
        hourly_old = conn.execute(
            'SELECT COUNT(*) FROM readings_hourly WHERE ts < ?',
            (now - 730 * one_day,)).fetchone()[0]
        assert raw_count == 1  # the 100-day-old raw row was pruned, the 1-day-old one kept
        assert hourly_old == 0  # the 800-day-old hourly row was pruned
        conn.close()


def test_rollup_and_prune_rolls_back_and_stays_usable_after_failure():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        one_day = 86400
        now = 200 * one_day
        poison_ts = now - 100 * one_day  # older than raw_days=90 -> will be pruned

        # A completed-hour minute row so the rollup INSERT has real work to do
        # before the later retention DELETE fails, plus the row that will be
        # pruned by the retention DELETE and trips the poison trigger below.
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'air_temperature'), now - 2 * 3600, 20.0, 20.0, 20.0, 1),
            (('zone', 'zone1', 'air_temperature'), poison_ts, 5.0, 5.0, 5.0, 1),
        ])

        # A trigger that raises a real SQLite error the moment the poison row
        # is deleted — simulating a genuine failure partway through
        # rollup_and_prune's transaction (after the rollup INSERT has already
        # run, before COMMIT).
        conn.execute('''
            CREATE TRIGGER poison_on_delete BEFORE DELETE ON readings
            WHEN OLD.ts = %d
            BEGIN
              SELECT RAISE(ABORT, 'simulated mid-transaction failure');
            END
        ''' % poison_ts)

        raised = False
        try:
            recorder.rollup_and_prune(conn, now, raw_days=90, hourly_days=730)
        except sqlite3.IntegrityError:
            raised = True
        assert raised  # the failure must propagate, not be swallowed

        # The whole transaction — including the rollup INSERT that ran
        # earlier in the same transaction — must have been rolled back.
        hourly_rows = conn.execute('SELECT COUNT(*) FROM readings_hourly').fetchone()[0]
        assert hourly_rows == 0

        # A wedged connection would raise OperationalError here ("cannot
        # start a transaction within a transaction"). It must not.
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'air_temperature'), now, 30.0, 30.0, 30.0, 1),
        ])  # must not raise
        conn.close()


def test_rollup_uses_weighted_average_not_naive_average():
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        # Unequal n across the two minute buckets: naive AVG(avg) would give
        # (10.0 + 30.0) / 2 = 20.0, but the correct weighted average
        # SUM(avg*n)/SUM(n) gives (10*1 + 30*3) / (1+3) = 25.0. The two must
        # differ, or this test wouldn't catch a regression to naive averaging.
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'air_temperature'), 3600, 10.0, 10.0, 10.0, 1),
            (('zone', 'zone1', 'air_temperature'), 3660, 30.0, 28.0, 32.0, 3),
        ])
        now = 3600 + 3600 + 3600  # well past the hour's end, so it's "completed"
        recorder.rollup_and_prune(conn, now, raw_days=90, hourly_days=730)
        rows = conn.execute('SELECT ts, avg, min, max, n FROM readings_hourly').fetchall()
        assert len(rows) == 1
        ts, avg, mn, mx, n = rows[0]
        assert ts == 3600
        assert avg == 25.0  # weighted: (10*1 + 30*3) / 4 -- naive (10+30)/2 = 20.0 would fail this
        assert mn == 10.0
        assert mx == 32.0
        assert n == 4
        conn.close()


def test_parse_topic_zone_metrics():
    assert recorder.parse_topic('greenhouse/zone1/air/temperature') == \
        ('zone', 'zone1', 'air_temperature')
    assert recorder.parse_topic('greenhouse/zone2/soil/moisture') == \
        ('zone', 'zone2', 'soil_moisture')
    assert recorder.parse_topic('greenhouse/zone1/light/lux') == \
        ('zone', 'zone1', 'light_lux')


def test_parse_topic_weather_metrics():
    assert recorder.parse_topic('greenhouse/weather/temperature') == \
        ('weather', None, 'temperature')
    assert recorder.parse_topic('greenhouse/weather/rain_mm_1h') == \
        ('weather', None, 'rain_mm_1h')


def test_parse_topic_ignores_unknown_topics():
    assert recorder.parse_topic('greenhouse/weather/forecast') is None  # JSON, not a scalar
    assert recorder.parse_topic('greenhouse/weather/alert') is None
    assert recorder.parse_topic('greenhouse/actuators/pump1/set') is None
    assert recorder.parse_topic('greenhouse/nodes/node1/status') is None


def test_load_config_uses_defaults_when_file_missing(monkeypatch):
    monkeypatch.setattr(recorder, 'RECORDER_CFG', '/nonexistent/path/recorder.json')
    cfg = recorder.load_config()
    assert cfg['flush_seconds'] == 60
    assert cfg['raw_days'] == 90
    assert cfg['hourly_days'] == 730


def test_load_config_merges_file_over_defaults(tmp_path, monkeypatch):
    cfg_file = tmp_path / 'recorder.json'
    cfg_file.write_text('{"flush_seconds": 30}')
    monkeypatch.setattr(recorder, 'RECORDER_CFG', str(cfg_file))
    cfg = recorder.load_config()
    assert cfg['flush_seconds'] == 30
    assert cfg['raw_days'] == 90  # untouched default


# ── Finding 2: MinuteBucketBuffer thread-safety ─────────────────────────────
def test_buffer_add_is_thread_safe_under_concurrent_adds():
    import threading
    buf = recorder.MinuteBucketBuffer()
    n_threads = 8
    adds_per_thread = 500
    # A mix of shared keys (contended across threads) and one per-thread
    # distinct key, so both the "same bucket" and "different bucket" paths
    # are exercised concurrently.
    shared_keys = [
        ('zone', 'zone1', 'air_temperature'),
        ('weather', None, 'temperature'),
    ]

    def worker(idx):
        shared_key = shared_keys[idx % len(shared_keys)]
        distinct_key = ('zone', f'zone{idx}', 'soil_moisture')
        for i in range(adds_per_thread):
            buf.add(shared_key, 1000, float(i))
            buf.add(distinct_key, 1000, float(i))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ready = buf.flush_all()
    total_n = sum(r[5] for r in ready)
    # Every add() call increments some bucket's count by exactly 1. If any
    # update is silently lost to a race, this sum comes up short. This
    # asserts on final-state consistency (deterministic), not on timing.
    assert total_n == n_threads * adds_per_thread * 2


def test_buffer_add_is_thread_safe_concurrently_with_flush_ready():
    import threading
    # Reproduces the actual race described in the design review: the MQTT
    # callback thread calling add() on a bucket at the same moment the main
    # loop's flush_ready() pops that same bucket. A plain "add from N
    # threads, then flush once after joining" pattern (see the test above)
    # rarely exposes this race under CPython's default GIL switch interval,
    # because the add()/pop() critical sections are each only a few
    # bytecodes wide. Lowering the switch interval for the duration of this
    # test makes thread interleaving frequent enough to reliably expose a
    # lost update when the lock is missing -- confirmed locally: this
    # reproduces lost updates in every run without the lock, and never with
    # it (see task-4-report.md for the raw counts).
    buf = recorder.MinuteBucketBuffer()
    key = ('zone', 'zone1', 'air_temperature')
    n_adds = 20000
    stop = threading.Event()
    collected = []

    def adder():
        for _ in range(n_adds):
            buf.add(key, 0, 1.0)  # minute_ts=0 -- already "ready" for any now >= 60
        stop.set()

    def flusher():
        while not stop.is_set():
            collected.extend(buf.flush_ready(now=1_000_000))
        collected.extend(buf.flush_ready(now=1_000_000))  # final drain

    original_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        t_add = threading.Thread(target=adder)
        t_flush = threading.Thread(target=flusher)
        t_add.start()
        t_flush.start()
        t_add.join()
        t_flush.join()
    finally:
        sys.setswitchinterval(original_interval)

    total_n = sum(r[5] for r in collected)
    # Every add() increments some bucket's count by exactly 1. If an update
    # is silently lost to the add()/flush_ready() race, this sum comes up
    # short -- a deterministic final-state check, not a timing assertion.
    assert total_n == n_adds


# ── Finding 1: crash-safety of the per-tick flush/rollup helpers ───────────
def test_flush_tick_logs_and_does_not_raise_on_write_failure(monkeypatch, capsys):
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        buf = recorder.MinuteBucketBuffer()
        buf.add(('zone', 'zone1', 'air_temperature'), 1000, 20.0)

        def broken_write_buckets(conn, series_ids, buckets):
            raise sqlite3.OperationalError('database is locked')

        monkeypatch.setattr(recorder, 'write_buckets', broken_write_buckets)

        recorder._flush_tick(conn, series_ids, buf, now=1060)  # must not raise

        out = capsys.readouterr().out
        assert '[recorder] ERROR' in out
        conn.close()


def test_flush_shutdown_logs_and_does_not_raise_on_write_failure(monkeypatch, capsys):
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        buf = recorder.MinuteBucketBuffer()
        buf.add(('zone', 'zone1', 'air_temperature'), 1000, 20.0)

        def broken_write_buckets(conn, series_ids, buckets):
            raise sqlite3.OperationalError('database is locked')

        monkeypatch.setattr(recorder, 'write_buckets', broken_write_buckets)

        recorder._flush_shutdown(conn, series_ids, buf)  # must not raise

        out = capsys.readouterr().out
        assert '[recorder] ERROR' in out
        conn.close()


def test_rollup_tick_logs_and_does_not_raise_on_failure(monkeypatch, capsys):
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))

        def broken_rollup(conn, now, raw_days, hourly_days):
            raise sqlite3.OperationalError('database is locked')

        monkeypatch.setattr(recorder, 'rollup_and_prune', broken_rollup)

        recorder._rollup_tick(conn, now=3600 * 3, raw_days=90, hourly_days=730)  # must not raise

        out = capsys.readouterr().out
        assert '[recorder] ERROR' in out
        conn.close()
