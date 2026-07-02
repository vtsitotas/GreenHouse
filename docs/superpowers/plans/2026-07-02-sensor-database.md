# Sensor Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local SQLite recorder on the Pi Zero W that durably stores zone/weather sensor history, exposes it to the Flutter app via HTTP, and gives `weather.py` stateful (duration-based) automation rules.

**Architecture:** A new dedicated systemd service (`greenhouse-recorder`, `pi/scripts/recorder.py`) subscribes to sensor/weather MQTT topics, buffers readings in RAM as 1-minute avg/min/max buckets, and flushes one batched transaction per minute to a WAL-mode SQLite database at `/var/lib/greenhouse/greenhouse.db`. It also rolls completed hours into an hourly table and prunes old rows on the same cadence. The existing Flask portal (`pi/portal/portal.py`) gets read-only history endpoints; `pi/scripts/weather.py` gets read-only access for duration-based rule evaluation. The Flutter app gets a thin HTTP history service and a chart screen reusing the existing hand-rolled `CustomPainter` chart pattern from `weather_screen.dart` (no new chart package).

**Tech Stack:** Python 3 stdlib `sqlite3`, `paho-mqtt` (apt package `python3-paho-mqtt`, same `CallbackAPIVersion.VERSION1` pattern as `pi/tools/simulator.py`), Flask (already a dependency), Dart/Flutter with `http` (already a dependency) and Riverpod.

## Global Constraints

- Config files live in `/etc/greenhouse/` (JSON), matching `weather.json`/`rules.json`/`device.json` convention.
- Data files live in `/var/lib/greenhouse/`, matching how Mosquitto uses `/var/lib/mosquitto/`.
- New systemd units run `User=pi`, `Restart=always`, with `NoNewPrivileges=yes` + `ProtectSystem=strict` sandboxing, matching `greenhouse-weather.service`.
- All Pi-side Python uses `print(f'[module] ...', flush=True)` for logging — no `logging` module, matching `weather.py`/`simulator.py`.
- Packages are installed via `apt-get`, not `pip`, per `pi/install.sh`'s existing convention — `python3-paho-mqtt` is a real Debian/Raspberry Pi OS package.
- Retention: 90 days at minute resolution, 730 days (2 years) at hourly resolution — exact values from the approved spec, configurable via `/etc/greenhouse/recorder.json`.
- Flush interval: 60 seconds (one batched SQLite transaction), not per-message writes — this is the core SD-card-wear protection and must not be changed casually.
- Retained MQTT messages must be skipped on ingest (`msg.retain == True` → discard) so recorder restarts don't re-record stale values.
- Spec doc: `docs/superpowers/specs/2026-07-02-sensor-database-design.md` — refer back to it for anything this plan doesn't spell out.

---

## File Structure

| File | Responsibility |
|---|---|
| `pi/scripts/recorder.py` (new) | MQTT-to-SQLite writer: minute-bucket buffering, schema bootstrap, flush, hourly rollup + retention, SIGTERM handling |
| `pi/systemd/greenhouse-recorder.service` (new) | systemd unit for the recorder |
| `pi/install.sh` (modify) | Package, directory, default config, unit install/enable/restart |
| `pi/scripts/selftest.sh` (modify) | Recorder health checks |
| `pi/scripts/prep_image.sh` (modify) | Wipe the DB before cloning |
| `pi/portal/portal.py` (modify) | `/api/history` + `/api/history/series` read-only endpoints |
| `pi/scripts/weather.py` (modify) | Duration-based rule evaluation against the recorder DB |
| `pi/systemd/greenhouse-weather.service` (modify) | `ReadWritePaths` for WAL-mode DB reads |
| `pi/tests/test_recorder.py` (new) | Unit tests for `MinuteBucketBuffer`, `parse_topic`, schema/write/rollup logic |
| `pi/tests/test_weather_rules.py` (new) | Unit tests for `duration_coverage` and duration-rule DB evaluation |
| `pi/tests/test_portal_history.py` (new) | Flask test-client tests for the new endpoints |
| `app/lib/models/history_point.dart` (new) | Data model for one history data point |
| `app/lib/services/history_service.dart` (new) | HTTP client for `/api/history*` |
| `app/lib/providers/history_provider.dart` (new) | Riverpod wiring for the history service |
| `app/lib/screens/history/history_screen.dart` (new) | Chart screen (reuses the `CustomPainter` pattern from `weather_screen.dart`) |
| `app/lib/screens/dashboard/zone_card.dart` (modify) | Tap-through navigation to the history screen |
| `app/lib/app.dart` (modify) | New `/history` route |

Running Pi-side tests requires `pytest` and `flask` importable on the **development machine** (not the Pi) — they are not added to `pi/tools/requirements.txt`, which is Pi-runtime-only. Run from repo root: `pip install pytest flask` once, then `python3 -m pytest pi/tests/ -v`.

---

### Task 1: Minute-bucket aggregation buffer

**Files:**
- Create: `pi/scripts/recorder.py`
- Test: `pi/tests/test_recorder.py`

**Interfaces:**
- Produces: `MinuteBucketBuffer` class with `.add(series_key: tuple, timestamp: int, value: float) -> None`, `.flush_ready(now: int) -> list[tuple]` (each tuple: `(series_key, minute_ts, avg, min, max, n)`), `.flush_all() -> list[tuple]` (same shape, ignores elapsed-time check, used on shutdown).

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest pi/tests/test_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError` or `AttributeError: module 'recorder' has no attribute 'MinuteBucketBuffer'` (file doesn't exist yet).

- [ ] **Step 3: Write the minimal implementation**

```python
#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — recorder.py
# Subscribes to sensor/weather MQTT topics and writes minute-resolution
# history to a local SQLite database, with hourly rollups and retention.
# ═══════════════════════════════════════════════════════════════════════════
import json
import os
import signal
import sqlite3
import time

import paho.mqtt.client as mqtt

RECORDER_CFG = '/etc/greenhouse/recorder.json'
MQTT_HOST = '127.0.0.1'
MQTT_PORT = 1883

DEFAULT_CONFIG = {
    'db_path': '/var/lib/greenhouse/greenhouse.db',
    'flush_seconds': 60,
    'raw_days': 90,
    'hourly_days': 730,
}

SUBSCRIBE_TOPICS = [
    'greenhouse/+/air/temperature',
    'greenhouse/+/air/humidity',
    'greenhouse/+/soil/moisture',
    'greenhouse/+/light/lux',
    'greenhouse/weather/+',
]

# ── Minute-bucket buffer ─────────────────────────────────────────────────────
class MinuteBucketBuffer:
    """Accumulates readings into per-(series, minute) avg/min/max/count buckets.

    Never writes to disk itself — the caller flushes ready buckets on a timer.
    """

    def __init__(self):
        self._buckets = {}  # (series_key, minute_ts) -> [sum, min, max, n]

    def add(self, series_key: tuple, timestamp: int, value: float):
        minute_ts = timestamp - (timestamp % 60)
        key = (series_key, minute_ts)
        if key not in self._buckets:
            self._buckets[key] = [value, value, value, 1]
        else:
            b = self._buckets[key]
            b[0] += value
            b[1] = min(b[1], value)
            b[2] = max(b[2], value)
            b[3] += 1

    def flush_ready(self, now: int) -> list:
        """Return and remove buckets whose minute has fully elapsed."""
        ready = []
        for key in list(self._buckets.keys()):
            series_key, minute_ts = key
            if minute_ts + 60 <= now:
                total, mn, mx, n = self._buckets.pop(key)
                ready.append((series_key, minute_ts, total / n, mn, mx, n))
        return ready

    def flush_all(self) -> list:
        """Return and remove every bucket regardless of elapsed time (shutdown path)."""
        ready = []
        for (series_key, minute_ts), (total, mn, mx, n) in self._buckets.items():
            ready.append((series_key, minute_ts, total / n, mn, mx, n))
        self._buckets.clear()
        return ready


if __name__ == '__main__':
    pass  # run() is added in Task 4
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest pi/tests/test_recorder.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/recorder.py pi/tests/test_recorder.py
git commit -m "feat: add minute-bucket aggregation buffer for sensor recorder"
```

---

### Task 2: SQLite schema and write path

**Files:**
- Modify: `pi/scripts/recorder.py`
- Test: `pi/tests/test_recorder.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (independent DB layer), but shares the `(kind, zone, metric)` series-key tuple shape used by `MinuteBucketBuffer`.
- Produces: `init_db(db_path: str) -> sqlite3.Connection`, `get_or_create_series_id(conn, kind: str, zone: str | None, metric: str) -> int`, `write_buckets(conn, series_ids: dict, buckets: list) -> None` (buckets in the same shape `MinuteBucketBuffer.flush_ready`/`flush_all` return).

- [ ] **Step 1: Write the failing tests**

```python
# pi/tests/test_recorder.py — append to the existing file
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest pi/tests/test_recorder.py -v`
Expected: FAIL with `AttributeError: module 'recorder' has no attribute 'init_db'`

- [ ] **Step 3: Write the minimal implementation**

Add to `pi/scripts/recorder.py`, after the `MinuteBucketBuffer` class and before the `if __name__ == '__main__':` line:

```python
# ── SQLite schema + write path ───────────────────────────────────────────────
_SCHEMA = '''
CREATE TABLE IF NOT EXISTS series (
  id     INTEGER PRIMARY KEY,
  kind   TEXT NOT NULL,
  zone   TEXT,
  metric TEXT NOT NULL,
  UNIQUE(kind, zone, metric)
);

CREATE TABLE IF NOT EXISTS readings (
  series_id INTEGER NOT NULL REFERENCES series(id),
  ts        INTEGER NOT NULL,
  avg REAL NOT NULL, min REAL NOT NULL, max REAL NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS readings_hourly (
  series_id INTEGER NOT NULL REFERENCES series(id),
  ts INTEGER NOT NULL,
  avg REAL NOT NULL, min REAL NOT NULL, max REAL NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
'''


def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # explicit BEGIN/COMMIT below
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.executescript(_SCHEMA)
    return conn


def get_or_create_series_id(conn: sqlite3.Connection, kind: str, zone, metric: str) -> int:
    row = conn.execute(
        'SELECT id FROM series WHERE kind=? AND zone IS ? AND metric=?',
        (kind, zone, metric)).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        'INSERT INTO series (kind, zone, metric) VALUES (?, ?, ?)',
        (kind, zone, metric))
    return cur.lastrowid


def write_buckets(conn: sqlite3.Connection, series_ids: dict, buckets: list) -> None:
    """buckets: list of (series_key, ts, avg, min, max, n). One transaction for all rows."""
    if not buckets:
        return
    rows = []
    for series_key, ts, avg, mn, mx, n in buckets:
        kind, zone, metric = series_key
        if series_key not in series_ids:
            series_ids[series_key] = get_or_create_series_id(conn, kind, zone, metric)
        rows.append((series_ids[series_key], ts, avg, mn, mx, n))
    conn.execute('BEGIN')
    conn.executemany(
        'INSERT OR REPLACE INTO readings (series_id, ts, avg, min, max, n) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        rows)
    conn.execute('COMMIT')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest pi/tests/test_recorder.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/recorder.py pi/tests/test_recorder.py
git commit -m "feat: add SQLite schema bootstrap and batched write path"
```

---

### Task 3: Hourly rollup and retention

**Files:**
- Modify: `pi/scripts/recorder.py`
- Test: `pi/tests/test_recorder.py`

**Interfaces:**
- Consumes: `init_db`, `get_or_create_series_id`, `write_buckets` from Task 2.
- Produces: `rollup_and_prune(conn: sqlite3.Connection, now: int, raw_days: int, hourly_days: int) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# pi/tests/test_recorder.py — append to the existing file

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
            "SELECT series_id, ?, 20.0, 20.0, 20.0, 1 FROM series LIMIT 1",
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest pi/tests/test_recorder.py -v`
Expected: FAIL with `AttributeError: module 'recorder' has no attribute 'rollup_and_prune'`

- [ ] **Step 3: Write the minimal implementation**

Add to `pi/scripts/recorder.py`, after `write_buckets`:

```python
def _get_meta_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return int(row[0]) if row else default


def _set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        'INSERT INTO meta (key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (key, str(value)))


def rollup_and_prune(conn: sqlite3.Connection, now: int, raw_days: int, hourly_days: int) -> None:
    """Roll completed hours into readings_hourly, then prune rows past retention."""
    watermark = _get_meta_int(conn, 'rollup_watermark', default=0)
    current_hour_start = now - (now % 3600)
    # Only hours that have fully elapsed are "completed" — never roll up the
    # in-progress hour, or a late-arriving reading for it would be missed.
    rollup_end = current_hour_start  # exclusive upper bound

    conn.execute('BEGIN')
    if rollup_end > watermark:
        conn.execute('''
            INSERT INTO readings_hourly (series_id, ts, avg, min, max, n)
            SELECT series_id, ts - (ts % 3600) AS hour_ts,
                   SUM(avg * n) / SUM(n), MIN(min), MAX(max), SUM(n)
            FROM readings
            WHERE ts >= ? AND ts < ?
            GROUP BY series_id, hour_ts
            ON CONFLICT(series_id, ts) DO UPDATE SET
              avg=excluded.avg, min=excluded.min, max=excluded.max, n=excluded.n
        ''', (watermark, rollup_end))
        _set_meta(conn, 'rollup_watermark', rollup_end)
    conn.execute('DELETE FROM readings WHERE ts < ?', (now - raw_days * 86400,))
    conn.execute('DELETE FROM readings_hourly WHERE ts < ?', (now - hourly_days * 86400,))
    conn.execute('COMMIT')
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest pi/tests/test_recorder.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/recorder.py pi/tests/test_recorder.py
git commit -m "feat: add hourly rollup and retention pruning"
```

---

### Task 4: MQTT ingest, topic parsing, and main loop

**Files:**
- Modify: `pi/scripts/recorder.py`
- Test: `pi/tests/test_recorder.py`

**Interfaces:**
- Consumes: `MinuteBucketBuffer`, `init_db`, `write_buckets`, `rollup_and_prune` from Tasks 1-3.
- Produces: `parse_topic(topic: str) -> tuple | None` (pure, tested), `load_config() -> dict`, `run() -> None` (assembles everything; not unit tested — verified manually per Step 6 below).

- [ ] **Step 1: Write the failing tests**

```python
# pi/tests/test_recorder.py — append to the existing file

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest pi/tests/test_recorder.py -v`
Expected: FAIL with `AttributeError: module 'recorder' has no attribute 'parse_topic'`

- [ ] **Step 3: Write the minimal implementation**

Add to `pi/scripts/recorder.py`, after `rollup_and_prune`, then replace the `if __name__ == '__main__': pass` stub at the bottom:

```python
# ── Topic parsing ─────────────────────────────────────────────────────────────
_ZONE_METRIC_GROUPS = {'air', 'soil', 'light'}
_WEATHER_METRICS = {'temperature', 'humidity', 'wind_kmh', 'uv_index', 'rain_mm_1h'}


def parse_topic(topic: str):
    """Return (kind, zone, metric) for a recordable topic, else None."""
    parts = topic.split('/')
    if len(parts) == 4 and parts[0] == 'greenhouse' and parts[2] in _ZONE_METRIC_GROUPS:
        zone, group, field = parts[1], parts[2], parts[3]
        return ('zone', zone, f'{group}_{field}')
    if len(parts) == 3 and parts[0] == 'greenhouse' and parts[1] == 'weather':
        metric = parts[2]
        if metric in _WEATHER_METRICS:
            return ('weather', None, metric)
    return None


# ── Config ───────────────────────────────────────────────────────────────────
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(RECORDER_CFG) as f:
            cfg.update(json.load(f))
    except Exception as e:
        print(f'[recorder] WARN: using defaults, cannot load config: {e}', flush=True)
    return cfg


# ── Main loop ────────────────────────────────────────────────────────────────
_running = True


def _handle_signal(sig, frame):
    global _running
    print(f'[recorder] Signal {sig} received, stopping.', flush=True)
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def run():
    cfg = load_config()
    conn = init_db(cfg['db_path'])
    buffer = MinuteBucketBuffer()
    series_ids = {}

    def on_connect(client, userdata, flags, rc, properties=None):
        for topic in SUBSCRIBE_TOPICS:
            client.subscribe(topic)
        print(f'[recorder] Connected, subscribed to {len(SUBSCRIBE_TOPICS)} topic patterns', flush=True)

    def on_message(client, userdata, msg):
        if msg.retain:
            return  # skip stale retained replay on (re)connect
        parsed = parse_topic(msg.topic)
        if parsed is None:
            return
        try:
            value = float(msg.payload.decode().strip())
        except (ValueError, UnicodeDecodeError):
            return
        buffer.add(parsed, int(time.time()), value)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-recorder')
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    print(f'[recorder] Starting — flush every {cfg["flush_seconds"]}s, '
          f'db={cfg["db_path"]}', flush=True)

    last_flush = time.time()
    last_rollup = 0.0
    while _running:
        time.sleep(1)
        now = time.time()
        if now - last_flush >= cfg['flush_seconds']:
            write_buckets(conn, series_ids, buffer.flush_ready(int(now)))
            last_flush = now
            if now - last_rollup >= 3600:
                rollup_and_prune(conn, int(now), cfg['raw_days'], cfg['hourly_days'])
                last_rollup = now

    print('[recorder] Stopping, flushing remaining buckets...', flush=True)
    write_buckets(conn, series_ids, buffer.flush_all())
    conn.close()
    client.loop_stop()
    client.disconnect()
    print('[recorder] Stopped.', flush=True)


if __name__ == '__main__':
    run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest pi/tests/test_recorder.py -v`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/recorder.py pi/tests/test_recorder.py
git commit -m "feat: wire up MQTT ingest and recorder main loop"
```

- [ ] **Step 6: Manual verification against the simulator**

This step has no automated test — `run()` needs a live Mosquitto broker, which is exactly what `pi/tools/simulator.py` provides. Do this on the Pi (or any machine with Mosquitto + paho-mqtt installed and pointed at loopback):

```bash
# Terminal 1 — start recorder against a scratch DB
mkdir -p /tmp/greenhouse-test
cat > /tmp/greenhouse-test/recorder.json <<'EOF'
{"db_path": "/tmp/greenhouse-test/test.db", "flush_seconds": 10, "raw_days": 90, "hourly_days": 730}
EOF
RECORDER_CFG_OVERRIDE=/tmp/greenhouse-test/recorder.json python3 -c "
import sys; sys.path.insert(0, 'pi/scripts')
import recorder
recorder.RECORDER_CFG = '/tmp/greenhouse-test/recorder.json'
recorder.run()
"

# Terminal 2 — feed it fake data every 5s
python3 pi/tools/simulator.py --interval 5

# Terminal 3 — after ~30s, confirm rows are landing
sqlite3 /tmp/greenhouse-test/test.db "SELECT s.kind, s.zone, s.metric, COUNT(*) FROM readings r JOIN series s ON s.id=r.series_id GROUP BY 1,2,3;"
```

Expected: rows for `zone1`/`zone2`/`zone3` × `air_temperature`/`air_humidity`/`soil_moisture`/`light_lux`, growing roughly one row per series per minute. Ctrl+C the recorder and confirm it prints `[recorder] Stopped.` within a couple seconds (graceful SIGINT/SIGTERM shutdown).

---

### Task 5: systemd unit and install.sh integration

**Files:**
- Create: `pi/systemd/greenhouse-recorder.service`
- Modify: `pi/install.sh:21-28` (package list), `pi/install.sh:30-33` (directories), `pi/install.sh:79-84` (systemd unit copies), `pi/install.sh:94` (enable list), `pi/install.sh:106-113` (after the weather.json default-config block), `pi/install.sh:136-139` (restart block)

**Interfaces:**
- Consumes: nothing (ops/deployment task, no code interface).
- Produces: the `greenhouse-recorder` systemd unit other tasks' selftest/prep_image checks reference by name.

- [ ] **Step 1: Create the systemd unit**

```ini
# pi/systemd/greenhouse-recorder.service
[Unit]
Description=Greenhouse Sensor History Recorder
After=network.target mosquitto.service
Requires=mosquitto.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/pi/greenhouse/scripts/recorder.py
Restart=always
RestartSec=10
User=pi
WorkingDirectory=/home/pi/greenhouse

TimeoutStopSec=15
KillSignal=SIGTERM

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/greenhouse

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Add the apt package**

In `pi/install.sh`, modify the `apt-get install` block (currently lines 21-28):

```bash
apt-get install -y -qq \
  mosquitto mosquitto-clients \
  python3-flask \
  python3-paho-mqtt \
  openssl \
  dnsmasq-base \
  iptables \
  rfkill \
  avahi-daemon
```

- [ ] **Step 3: Add the data directory**

In `pi/install.sh`, modify the directories block (currently lines 30-33):

```bash
echo "==> Creating directories..."
# /var/log/journal makes journald persistent across reboots (so a failed
# boot-time service can be diagnosed after the fact, e.g. on a shipped unit).
mkdir -p /etc/greenhouse /etc/mosquitto/certs /var/lib/mosquitto /var/log/journal /var/lib/greenhouse
chown pi:pi /var/lib/greenhouse
```

- [ ] **Step 4: Copy the new systemd unit**

In `pi/install.sh`, modify the systemd unit copy block (currently lines 79-84):

```bash
echo "==> Installing systemd services..."
cp "$REPO"/systemd/greenhouse-firstboot.service      /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-portal.service         /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-ap.service             /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-wifi-watchdog.service  /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-weather.service        /etc/systemd/system/
cp "$REPO"/systemd/greenhouse-recorder.service       /etc/systemd/system/
```

- [ ] **Step 5: Add to the enable line**

In `pi/install.sh`, modify (currently line 94):

```bash
systemctl enable greenhouse-firstboot greenhouse-portal greenhouse-ap greenhouse-wifi-watchdog greenhouse-weather greenhouse-recorder >/dev/null 2>&1
```

- [ ] **Step 6: Add the default recorder config**

In `pi/install.sh`, insert immediately after the existing rules.json block (currently ending at line 128), before the `echo "==> Generating this unit's MQTT credentials..."` line:

```bash
echo "==> Writing default recorder config..."
[ -f /etc/greenhouse/recorder.json ] || cat > /etc/greenhouse/recorder.json << 'EOF'
{
  "db_path": "/var/lib/greenhouse/greenhouse.db",
  "flush_seconds": 60,
  "raw_days": 90,
  "hourly_days": 730
}
EOF
```

- [ ] **Step 7: Add to the restart block**

In `pi/install.sh`, modify (currently lines 136-139):

```bash
echo "==> Restarting services..."
systemctl restart mosquitto
systemctl restart greenhouse-portal
systemctl restart greenhouse-weather
systemctl restart greenhouse-recorder
```

- [ ] **Step 8: Give weather.py read access to the recorder DB**

In `pi/systemd/greenhouse-weather.service`, modify the `ReadWritePaths` line (currently line 27) — this is required in this task, not Task 7, because `ProtectSystem=strict` blocks the path entirely until this is added, and Task 7's tests would otherwise silently pass locally (no sandboxing in a test run) while failing on the real Pi:

```ini
ReadWritePaths=/tmp /etc/greenhouse /var/lib/greenhouse
```

- [ ] **Step 9: Commit**

```bash
git add pi/systemd/greenhouse-recorder.service pi/systemd/greenhouse-weather.service pi/install.sh
git commit -m "feat: install and enable the greenhouse-recorder service"
```

- [ ] **Step 10: Manual verification (on the Pi only — do not run on a dev machine)**

```bash
sudo bash /home/pi/greenhouse/install.sh
systemctl status greenhouse-recorder --no-pager
journalctl -u greenhouse-recorder -n 20 --no-pager
```

Expected: `active (running)`, log shows `[recorder] Starting — flush every 60s, db=/var/lib/greenhouse/greenhouse.db` and `[recorder] Connected, subscribed to 5 topic patterns`.

---

### Task 6: Portal history HTTP endpoints

**Files:**
- Modify: `pi/portal/portal.py`
- Test: `pi/tests/test_portal_history.py`

**Interfaces:**
- Consumes: the `series`/`readings`/`readings_hourly` schema from Task 2 (read-only, no import dependency on `recorder.py` — portal opens the DB file directly).
- Produces: `GET /api/history/series`, `GET /api/history` — the Flutter `history_service.dart` in Task 9 depends on these exact response shapes.

- [ ] **Step 1: Write the failing tests**

```python
# pi/tests/test_portal_history.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest pi/tests/test_portal_history.py -v`
Expected: FAIL with `AttributeError: module 'portal' has no attribute '_RECORDER_DB'`

- [ ] **Step 3: Write the minimal implementation**

In `pi/portal/portal.py`, add `import sqlite3` after the existing `import os` line (line 15), keeping the block alphabetically ordered — `import subprocess` and `import time` (lines 16-17) are already there and stay unchanged:

```python
import json
import os
import sqlite3
import subprocess
import time
```

Then add, after the `_load_hivemq` function (after line 58, before `_validate`):

```python
_RECORDER_DB = "/var/lib/greenhouse/greenhouse.db"


def _history_db() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_RECORDER_DB}?mode=ro", uri=True)
```

Then add, after the `/pair` route (after line 205, before `if __name__ == "__main__":`):

```python
@app.route("/api/history/series")
def history_series():
    try:
        conn = _history_db()
        rows = conn.execute(
            "SELECT kind, zone, metric FROM series ORDER BY kind, zone, metric").fetchall()
        conn.close()
        return jsonify([{"kind": r[0], "zone": r[1], "metric": r[2]} for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history")
def history():
    zone = request.args.get("zone")
    kind = request.args.get("kind") or ("zone" if zone else None)
    metric = request.args.get("metric")
    try:
        hours = float(request.args.get("hours", 24))
    except ValueError:
        return jsonify({"error": "hours must be a number"}), 400
    if not metric or not kind:
        return jsonify({"error": "metric and (zone or kind) are required"}), 400

    table = "readings" if hours <= 48 else "readings_hourly"
    resolution = "minute" if table == "readings" else "hour"
    cutoff = int(time.time() - hours * 3600)
    try:
        conn = _history_db()
        row = conn.execute(
            "SELECT id FROM series WHERE kind=? AND zone IS ? AND metric=?",
            (kind, zone, metric)).fetchone()
        if row is None:
            conn.close()
            return jsonify({"zone": zone, "metric": metric,
                             "resolution": resolution, "points": []})
        series_id = row[0]
        pts = conn.execute(
            f"SELECT ts, avg, min, max FROM {table} WHERE series_id=? AND ts >= ? ORDER BY ts",
            (series_id, cutoff)).fetchall()
        conn.close()
        return jsonify({
            "zone": zone,
            "metric": metric,
            "resolution": resolution,
            "points": [[p[0], p[1], p[2], p[3]] for p in pts],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest pi/tests/test_portal_history.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add pi/portal/portal.py pi/tests/test_portal_history.py
git commit -m "feat: add read-only history endpoints to the portal"
```

---

### Task 7: Duration-based automation rules in weather.py

**Files:**
- Modify: `pi/scripts/weather.py:99-152` (rules section), `pi/install.sh` (default rules.json example — optional doc update, not required for function)
- Test: `pi/tests/test_weather_rules.py`

**Interfaces:**
- Consumes: `init_db`-created schema (read-only) from Task 2; does not import `recorder.py` (avoids coupling two independently-deployed services), opens the DB directly the same way `portal.py` does.
- Produces: `duration_coverage(values: list[float], op: str, threshold: float, expected_buckets: int) -> tuple[bool, float]` (pure, tested), `eval_duration_rule(conn, zone, metric, op, threshold, duration_minutes, now) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# pi/tests/test_weather_rules.py
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
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        now = 100000
        recorder.write_buckets(conn, series_ids, [
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest pi/tests/test_weather_rules.py -v`
Expected: FAIL with `AttributeError: module 'weather' has no attribute 'duration_coverage'`

- [ ] **Step 3: Write the minimal implementation**

In `pi/scripts/weather.py`, add `import sqlite3` to the imports block (line 7, alongside `import json`):

```python
import json
import os
import signal
import socket
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
```

Add the recorder DB path constant near the other config paths (after line 20, `DEVICE_CFG`):

```python
RECORDER_DB  = '/var/lib/greenhouse/greenhouse.db'
```

Replace the `eval_rules` function (lines 107-152) with:

```python
def duration_coverage(values: list, op: str, threshold: float, expected_buckets: int):
    """Given avg values in the lookback window, return (fires, coverage_ratio).

    Fires when coverage is at least 80% of the expected minute-buckets AND
    every present bucket satisfies the condition.
    """
    ops = {
        '>':  lambda a, b: a > b,
        '<':  lambda a, b: a < b,
        '>=': lambda a, b: a >= b,
        '<=': lambda a, b: a <= b,
        '==': lambda a, b: a == b,
    }
    op_fn = ops.get(op)
    if op_fn is None or expected_buckets <= 0:
        return False, 0.0
    coverage = len(values) / expected_buckets
    if not values:
        return False, coverage
    all_match = all(op_fn(v, threshold) for v in values)
    return (coverage >= 0.8 and all_match), coverage


def eval_duration_rule(conn: sqlite3.Connection, zone, metric: str, op: str,
                        threshold: float, duration_minutes: int, now: int) -> bool:
    kind = 'zone' if zone else 'weather'
    row = conn.execute(
        'SELECT id FROM series WHERE kind=? AND zone IS ? AND metric=?',
        (kind, zone, metric)).fetchone()
    if row is None:
        return False
    series_id = row[0]
    cutoff = now - duration_minutes * 60
    values = [r[0] for r in conn.execute(
        'SELECT avg FROM readings WHERE series_id=? AND ts >= ?',
        (series_id, cutoff)).fetchall()]
    fires, _ = duration_coverage(values, op, threshold, expected_buckets=duration_minutes)
    return fires


_last_fired: dict[str, float] = {}  # rule id -> monotonic time last fired


def eval_rules(rules: list, metrics: dict):
    """Evaluate each rule; publish actuator commands and alerts as needed."""
    ops = {
        '>':  lambda a, b: a > b,
        '<':  lambda a, b: a < b,
        '>=': lambda a, b: a >= b,
        '<=': lambda a, b: a <= b,
        '==': lambda a, b: a == b,
    }

    def _fire(rule, message):
        action   = rule['action']
        actuator = action['actuator']
        command  = action['command']
        topic = f'greenhouse/actuators/{actuator}/set'
        print(f'[weather] Rule "{rule.get("name")}" triggered → {topic} {command}', flush=True)
        mqtt_publish(topic, command)
        alert = {
            'type': rule.get('id', 'rule'),
            'message': message,
            'severity': 'warning',
            'rule_id': rule.get('id'),
        }
        mqtt_publish('greenhouse/weather/alert', json.dumps(alert))

    for rule in rules:
        if not rule.get('enabled', True):
            continue
        try:
            trigger  = rule['trigger']
            metric   = trigger['metric']
            op_name  = trigger['op']
            thresh   = float(trigger['value'])
            duration_minutes = trigger.get('duration_minutes')

            if duration_minutes:
                rule_id = rule.get('id', '')
                cooldown_minutes = rule.get('cooldown_minutes', 0)
                last = _last_fired.get(rule_id, 0.0)
                if time.monotonic() - last < cooldown_minutes * 60:
                    continue
                zone, sep, bare_metric = metric.partition('/')
                if not sep:
                    zone, bare_metric = None, metric
                try:
                    conn = sqlite3.connect(f'file:{RECORDER_DB}?mode=ro', uri=True)
                except Exception as e:
                    print(f'[weather] WARN: cannot open recorder DB for duration rule '
                          f'{rule.get("id")}: {e}', flush=True)
                    continue
                try:
                    fired = eval_duration_rule(
                        conn, zone, bare_metric, op_name, thresh,
                        int(duration_minutes), int(time.time()))
                finally:
                    conn.close()
                if fired:
                    _last_fired[rule_id] = time.monotonic()
                    _fire(rule, f'{rule.get("name","Rule")} triggered '
                                f'({metric} {op_name} {thresh} sustained for '
                                f'{int(duration_minutes)} min)')
                continue

            # Live-metric rules (unchanged behavior)
            val = metrics.get(metric)
            if val is None:
                continue
            op_fn = ops.get(op_name)
            if op_fn is None:
                print(f'[weather] WARN: unknown op "{op_name}" in rule {rule.get("id")}', flush=True)
                continue
            if op_fn(val, thresh):
                _fire(rule, f'{rule.get("name","Rule")} triggered '
                            f'({metric} {op_name} {thresh}, current: {val:.1f})')
        except Exception as e:
            print(f'[weather] WARN: rule eval error ({rule.get("id")}): {e}', flush=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest pi/tests/test_weather_rules.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full test suite to confirm nothing else broke**

Run: `python3 -m pytest pi/tests/ -v`
Expected: all tests from Tasks 1-7 pass (26 total).

- [ ] **Step 6: Commit**

```bash
git add pi/scripts/weather.py pi/tests/test_weather_rules.py
git commit -m "feat: add duration-based stateful automation rules"
```

---

### Task 8: Selftest and image-prep integration

**Files:**
- Modify: `pi/scripts/selftest.sh` (lines 9, 14, plus a new section)
- Modify: `pi/scripts/prep_image.sh` (add to the wipe block, currently lines 19-26)

**Interfaces:**
- Consumes: nothing (ops scripts, no code interface).
- Produces: nothing consumed by later tasks — this is the terminal ops-verification task for the Pi side.

- [ ] **Step 1: Add recorder to the enabled/active service checks**

In `pi/scripts/selftest.sh`, modify line 9:

```bash
for s in greenhouse-firstboot greenhouse-portal greenhouse-ap greenhouse-wifi-watchdog greenhouse-recorder mosquitto; do
```

And modify line 14:

```bash
for s in greenhouse-portal greenhouse-recorder mosquitto; do
```

- [ ] **Step 2: Add a recorder database section**

In `pi/scripts/selftest.sh`, insert a new section after the `== portal ==` section (after line 44, before `== mDNS ==`):

```bash
echo "== recorder database =="
[ -f /var/lib/greenhouse/greenhouse.db ] && ok "greenhouse.db present" || no "greenhouse.db missing"
python3 - <<'PYEOF'
import sqlite3, time, sys
try:
    conn = sqlite3.connect('file:/var/lib/greenhouse/greenhouse.db?mode=ro', uri=True)
    integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
    assert integrity == 'ok', f'integrity_check returned {integrity}'
    recent = conn.execute(
        'SELECT COUNT(*) FROM readings WHERE ts >= ?',
        (int(time.time()) - 600,)).fetchone()[0]
    assert recent > 0, 'no readings in the last 10 minutes'
    print('       [ OK ] db integrity ok, recent readings present')
except Exception as e:
    print(f'       [FAIL] {e}', file=sys.stderr)
    sys.exit(1)
PYEOF
[ $? -eq 0 ] && ok "recorder db healthy" || no "recorder db unhealthy"
curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:80/api/history/series | grep -q '200' && ok "history endpoint responding" || no "history endpoint not responding"
```

- [ ] **Step 3: Wipe the database before cloning**

In `pi/scripts/prep_image.sh`, add to the wipe block (currently lines 19-26), right after the mosquitto certs `rm -f` line (line 26):

```bash
echo "[prep] wiping sensor history database..."
rm -f /var/lib/greenhouse/greenhouse.db /var/lib/greenhouse/greenhouse.db-wal /var/lib/greenhouse/greenhouse.db-shm
```

- [ ] **Step 4: Commit**

```bash
git add pi/scripts/selftest.sh pi/scripts/prep_image.sh
git commit -m "feat: add recorder checks to selftest and DB wipe to prep_image"
```

- [ ] **Step 5: Manual verification (on the Pi only)**

```bash
sudo bash /home/pi/greenhouse/scripts/selftest.sh
```

Expected: `== recorder database ==` section shows all `[ OK ]`, and the final line reads `RESULT: N passed, 0 failed`.

---

### Task 9: Flutter history data layer

**Files:**
- Create: `app/lib/models/history_point.dart`
- Create: `app/lib/services/history_service.dart`
- Test: `app/test/services/history_service_test.dart`

**Interfaces:**
- Consumes: `ConnectionConfig.lanHost` (existing model, `app/lib/models/connection_config.dart:2`) for the base URL.
- Produces: `HistoryPoint` class (`ts`, `avg`, `min`, `max` fields + `DateTime get time`), `HistorySeries` class (`kind`, `zone`, `metric` fields), `HistoryService.fetchPoints({required String lanHost, String? zone, String? kind, required String metric, double hours = 24}) -> Future<List<HistoryPoint>>`, `HistoryService.fetchSeries({required String lanHost}) -> Future<List<HistorySeries>>` — Task 10's provider depends on both method signatures exactly.

- [ ] **Step 1: Write the model (no test needed — plain data class, exercised by the service test)**

```dart
// app/lib/models/history_point.dart
class HistoryPoint {
  final DateTime time;
  final double avg;
  final double min;
  final double max;

  const HistoryPoint({
    required this.time,
    required this.avg,
    required this.min,
    required this.max,
  });

  factory HistoryPoint.fromJson(List<dynamic> json) => HistoryPoint(
        time: DateTime.fromMillisecondsSinceEpoch((json[0] as num).toInt() * 1000),
        avg: (json[1] as num).toDouble(),
        min: (json[2] as num).toDouble(),
        max: (json[3] as num).toDouble(),
      );
}

class HistorySeries {
  final String kind;
  final String? zone;
  final String metric;

  const HistorySeries({required this.kind, required this.zone, required this.metric});

  factory HistorySeries.fromJson(Map<String, dynamic> json) => HistorySeries(
        kind: json['kind'] as String,
        zone: json['zone'] as String?,
        metric: json['metric'] as String,
      );
}
```

- [ ] **Step 2: Write the failing test for `HistoryService`**

```dart
// app/test/services/history_service_test.dart
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:greenhouse_app/services/history_service.dart';

void main() {
  group('HistoryService', () {
    test('fetchPoints parses a successful response', () async {
      final client = MockClient((request) async {
        expect(request.url.path, '/api/history');
        expect(request.url.queryParameters['zone'], 'zone1');
        expect(request.url.queryParameters['metric'], 'air_temperature');
        return http.Response(
          jsonEncode({
            'zone': 'zone1',
            'metric': 'air_temperature',
            'resolution': 'minute',
            'points': [
              [1000, 20.0, 19.0, 21.0],
              [1060, 22.0, 21.0, 23.0],
            ],
          }),
          200,
        );
      });
      final service = HistoryService(client: client);
      final points = await service.fetchPoints(
        lanHost: 'greenhouse.local',
        zone: 'zone1',
        metric: 'air_temperature',
        hours: 24,
      );
      expect(points.length, 2);
      expect(points[0].avg, 20.0);
      expect(points[1].max, 23.0);
    });

    test('fetchPoints throws on non-200 response', () async {
      final client = MockClient((request) async => http.Response('error', 500));
      final service = HistoryService(client: client);
      expect(
        () => service.fetchPoints(lanHost: 'greenhouse.local', zone: 'zone1', metric: 'x'),
        throwsA(isA<Exception>()),
      );
    });

    test('fetchSeries parses a successful response', () async {
      final client = MockClient((request) async {
        expect(request.url.path, '/api/history/series');
        return http.Response(
          jsonEncode([
            {'kind': 'zone', 'zone': 'zone1', 'metric': 'air_temperature'},
            {'kind': 'weather', 'zone': null, 'metric': 'temperature'},
          ]),
          200,
        );
      });
      final service = HistoryService(client: client);
      final series = await service.fetchSeries(lanHost: 'greenhouse.local');
      expect(series.length, 2);
      expect(series[0].zone, 'zone1');
      expect(series[1].zone, isNull);
    });
  });
}
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd app && flutter test test/services/history_service_test.dart`
Expected: FAIL — `Error: Couldn't resolve the package 'http/testing.dart'` or `Target of URI doesn't exist: 'package:greenhouse_app/services/history_service.dart'` (file doesn't exist yet, and `http`'s `testing.dart` sub-import needs the `http` package, already a dependency).

- [ ] **Step 4: Write the minimal implementation**

```dart
// app/lib/services/history_service.dart
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:greenhouse_app/models/history_point.dart';

class HistoryService {
  final http.Client client;
  const HistoryService({http.Client? client}) : client = client ?? const _DefaultClient();

  Future<List<HistoryPoint>> fetchPoints({
    required String lanHost,
    String? zone,
    String? kind,
    required String metric,
    double hours = 24,
  }) async {
    final params = <String, String>{
      'metric': metric,
      'hours': hours.toString(),
      if (zone != null) 'zone': zone,
      if (kind != null) 'kind': kind,
    };
    final uri = Uri.http(lanHost, '/api/history', params);
    final resp = await client.get(uri);
    if (resp.statusCode != 200) {
      throw Exception('History fetch failed: ${resp.statusCode}');
    }
    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    final points = (data['points'] as List).cast<List<dynamic>>();
    return points.map(HistoryPoint.fromJson).toList();
  }

  Future<List<HistorySeries>> fetchSeries({required String lanHost}) async {
    final uri = Uri.http(lanHost, '/api/history/series');
    final resp = await client.get(uri);
    if (resp.statusCode != 200) {
      throw Exception('Series fetch failed: ${resp.statusCode}');
    }
    final data = jsonDecode(resp.body) as List;
    return data.cast<Map<String, dynamic>>().map(HistorySeries.fromJson).toList();
  }
}

class _DefaultClient extends http.BaseClient {
  const _DefaultClient();
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      http.Client().send(request);
}
```

Note: `http.testing.dart`'s `MockClient` is part of the `http` package itself (no new dependency needed) — confirm `app/pubspec.yaml` still lists `http: ^1.2.0` under `dependencies` (it already does, line 19).

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd app && flutter test test/services/history_service_test.dart`
Expected: 3 tests passed

- [ ] **Step 6: Commit**

```bash
git add app/lib/models/history_point.dart app/lib/services/history_service.dart app/test/services/history_service_test.dart
git commit -m "feat: add history data model and HTTP service"
```

---

### Task 10: Flutter history provider, chart screen, and navigation

**Files:**
- Create: `app/lib/providers/history_provider.dart`
- Create: `app/lib/screens/history/history_screen.dart`
- Modify: `app/lib/screens/dashboard/zone_card.dart` (tap-through)
- Modify: `app/lib/app.dart:19-40` (new route)

**Interfaces:**
- Consumes: `HistoryService` (Task 9), `ConnectionConfig` (`app/lib/models/connection_config.dart`), `pairingServiceProvider` (`app/lib/services/pairing_service.dart:26`).
- Produces: `historyServiceProvider`, `historyPointsProvider` (a `.family` provider keyed by `(zone, metric)`), `HistoryScreen` widget taking `zone` and `metric` route params.

- [ ] **Step 1: Write the provider**

```dart
// app/lib/providers/history_provider.dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/services/history_service.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

final historyServiceProvider = Provider((_) => const HistoryService());

class HistoryQuery {
  final String zone;
  final String metric;
  const HistoryQuery({required this.zone, required this.metric});

  @override
  bool operator ==(Object other) =>
      other is HistoryQuery && other.zone == zone && other.metric == metric;
  @override
  int get hashCode => Object.hash(zone, metric);
}

final historyPointsProvider =
    FutureProvider.family<List<HistoryPoint>, HistoryQuery>((ref, query) async {
  final config = await ref.read(pairingServiceProvider).loadConfig();
  if (config == null) return [];
  final service = ref.read(historyServiceProvider);
  return service.fetchPoints(
    lanHost: config.lanHost,
    zone: query.zone,
    metric: query.metric,
    hours: 24,
  );
});
```

This is not independently unit tested (it's thin Riverpod wiring over `HistoryService`, already tested in Task 9, and `pairingServiceProvider`, already exercised elsewhere in the app) — it's verified via the manual run in Step 5 below, matching how `forecastProvider`/`rulesProvider` in `app/lib/providers/connection_provider.dart` are handled.

- [ ] **Step 2: Write the chart screen**

This reuses the exact `CustomPainter` line-chart pattern from `_ForecastPainter` in `app/lib/screens/weather/weather_screen.dart:389-457` — no new chart dependency.

```dart
// app/lib/screens/history/history_screen.dart
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/providers/history_provider.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class HistoryScreen extends ConsumerWidget {
  final String zone;
  final String metric;
  const HistoryScreen({required this.zone, required this.metric, super.key});

  String get _title {
    final zoneLabel = zone.startsWith('zone') ? 'Zone ${zone.substring(4)}' : zone;
    final metricLabel = metric.replaceAll('_', ' ');
    return '$zoneLabel — $metricLabel';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pointsAsync =
        ref.watch(historyPointsProvider(HistoryQuery(zone: zone, metric: metric)));
    return Scaffold(
      appBar: AppBar(title: Text(_title)),
      body: pointsAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Text('Could not load history.\n$e', textAlign: TextAlign.center),
          ),
        ),
        data: (points) {
          if (points.isEmpty) {
            return const Center(child: Text('No history yet for this sensor.'));
          }
          return Padding(
            padding: const EdgeInsets.all(16),
            child: Card(
              elevation: 4,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Last 24 Hours',
                        style: Theme.of(context).textTheme.titleSmall
                            ?.copyWith(fontWeight: FontWeight.bold)),
                    const SizedBox(height: 12),
                    SizedBox(
                      height: 220,
                      child: CustomPaint(
                        size: const Size(double.infinity, 220),
                        painter: _HistoryPainter(points: points),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _HistoryPainter extends CustomPainter {
  final List<HistoryPoint> points;
  const _HistoryPainter({required this.points});

  @override
  void paint(Canvas canvas, Size size) {
    if (points.isEmpty) return;
    final n = points.length;

    final minV = points.map((p) => p.min).reduce(math.min);
    final maxV = points.map((p) => p.max).reduce(math.max);
    final range = (maxV - minV).abs().clamp(1.0, double.infinity);

    double tx(int i) => n > 1 ? i * size.width / (n - 1) : size.width / 2;
    double ty(double v) => size.height - ((v - minV) / range) * size.height;

    final linePaint = Paint()
      ..color = AppColors.brandLight
      ..strokeWidth = 2.5
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final fillPaint = Paint()
      ..color = AppColors.brandLight.withAlpha(40)
      ..style = PaintingStyle.fill;

    final path = Path()..moveTo(tx(0), ty(points[0].avg));
    for (int i = 1; i < n; i++) {
      path.lineTo(tx(i), ty(points[i].avg));
    }
    final fill = Path.from(path)
      ..lineTo(tx(n - 1), size.height)
      ..lineTo(tx(0), size.height)
      ..close();
    canvas.drawPath(fill, fillPaint);
    canvas.drawPath(path, linePaint);
  }

  @override
  bool shouldRepaint(_HistoryPainter old) => old.points != points;
}
```

- [ ] **Step 3: Wire tap-through from `ZoneCard`**

In `app/lib/screens/dashboard/zone_card.dart`, add the `go_router` import and wrap the existing `Card` in a `GestureDetector` — modify the top imports (currently lines 1-2) and the `build` method (currently lines 14-45):

```dart
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class ZoneCard extends StatelessWidget {
  final String zone;
  final Map<String, double> readings;
  const ZoneCard({required this.zone, required this.readings, super.key});

  String get _title {
    if (zone.startsWith('zone')) return 'Zone ${zone.substring(4)}';
    return '${zone[0].toUpperCase()}${zone.substring(1)}';
  }

  @override
  Widget build(BuildContext context) {
    final soil = readings['soil/moisture'];
    final lowSoil = soil != null && soil < 30;
    return GestureDetector(
      onTap: () => context.push('/history/$zone/air_temperature'),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Text(_title, style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)),
              if (lowSoil) ...[
                const SizedBox(width: 8),
                const Icon(Icons.warning_amber_rounded, color: AppColors.warning, size: 18),
              ],
              const Spacer(),
              Icon(Icons.chevron_right, color: Colors.grey[400]),
            ]),
            const SizedBox(height: 12),
            Wrap(spacing: 16, runSpacing: 8, children: [
              if (readings['air/temperature'] != null)
                _Chip(Icons.thermostat, 'Temp', '${readings['air/temperature']!.toStringAsFixed(1)} °C'),
              if (readings['air/humidity'] != null)
                _Chip(Icons.water_drop, 'Humidity', '${readings['air/humidity']!.toStringAsFixed(0)} %'),
              if (soil != null)
                _Chip(Icons.grass, 'Soil', '${soil.toStringAsFixed(0)} %', color: lowSoil ? AppColors.warning : null),
              if (readings['light/lux'] != null)
                _Chip(Icons.wb_sunny, 'Light', '${readings['light/lux']!.toStringAsFixed(0)} lux'),
              if (readings['pressure'] != null)
                _Chip(Icons.speed, 'Pressure', '${readings['pressure']!.toStringAsFixed(0)} hPa'),
            ]),
          ]),
        ),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final Color? color;
  const _Chip(this.icon, this.label, this.value, {this.color});

  @override
  Widget build(BuildContext context) => Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 16, color: color ?? Theme.of(context).colorScheme.primary),
        const SizedBox(width: 4),
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: Theme.of(context).textTheme.labelSmall),
          Text(value, style: TextStyle(fontWeight: FontWeight.w600, color: color)),
        ]),
      ]);
}
```

Tapping always opens `air_temperature` history for now — a metric picker inside `HistoryScreen` is explicitly out of scope for this task (YAGNI: one metric per zone card tap is enough to prove the feature works end-to-end; a picker can be added later without touching this plan's other files).

- [ ] **Step 4: Add the route**

In `app/lib/app.dart`, add the import (alongside the other screen imports, after line 12) and the route (inside the `ShellRoute`'s `routes` list, currently lines 32-36):

```dart
import 'package:greenhouse_app/screens/history/history_screen.dart';
```

```dart
GoRoute(
  path: '/history/:zone/:metric',
  builder: (_, state) => HistoryScreen(
    zone: state.pathParameters['zone']!,
    metric: state.pathParameters['metric']!,
  ),
),
```

Add this inside the same `ShellRoute` `routes` list as `/dashboard`, `/devices`, etc. (currently lines 32-36), so it keeps the bottom navigation shell — matching how every other screen in the app is routed.

- [ ] **Step 5: Manual verification**

No automated widget test for this task (the chart painter and provider wiring are thin glue over already-tested units; a full widget test would require mocking the router + provider container + HTTP client, which is more scaffolding than the code it protects — consistent with the project's existing test coverage, which only unit-tests connection/parsing logic, not screen widgets).

```bash
cd app
flutter analyze   # confirm no new warnings/errors
flutter run       # on a device/emulator paired with a Pi (or simulator.py + recorder.py running)
```

In the running app: open Dashboard, tap a zone card, confirm it navigates to `/history/zone1/air_temperature` and either shows a line chart (if `recorder.py` has been running against `simulator.py` for a few minutes) or "No history yet for this sensor." (if the recorder DB is empty) — both are correct, expected states.

- [ ] **Step 6: Commit**

```bash
git add app/lib/providers/history_provider.dart app/lib/screens/history/history_screen.dart app/lib/screens/dashboard/zone_card.dart app/lib/app.dart
git commit -m "feat: add history chart screen reachable from dashboard zone cards"
```

---

## Explicitly not in this plan (per the spec's Future Work / deliberately deferred)

- The optional "recorder also handles `greenhouse/rules/update`/`greenhouse/rules/get`" adjacent fix (spec §8 step 9) — genuinely optional per the spec, skipped for YAGNI.
- Auth on `/api/history*`, MQTT-based remote history RPC, the `events` audit table, external ML/analytics sync — all explicitly out of scope per the spec's Future Work section.
- A metric picker inside `HistoryScreen` (Task 10 hardcodes `air_temperature` from the zone card tap) — small, isolated follow-up if wanted later.
