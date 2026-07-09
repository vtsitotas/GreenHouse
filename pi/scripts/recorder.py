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
import threading
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

# Lets the app fetch chart data over MQTT when it's on the HiveMQ Cloud path
# (remote) instead of LAN, where the portal's HTTP /api/history isn't
# reachable — HiveMQ only bridges MQTT, not HTTP.
HISTORY_REQUEST_TOPIC = 'greenhouse/history/request'
HISTORY_RESPONSE_PREFIX = 'greenhouse/history/response/'

# ── Minute-bucket buffer ─────────────────────────────────────────────────────
class MinuteBucketBuffer:
    """Accumulates readings into per-(series, minute) avg/min/max/count buckets.

    Never writes to disk itself — the caller flushes ready buckets on a timer.
    """

    def __init__(self):
        self._buckets = {}  # (series_key, minute_ts) -> [sum, min, max, n]
        # add() runs on the paho-mqtt background thread (via on_message),
        # while flush_ready()/flush_all() run on the main thread's loop.
        # Without this lock, a concurrent add() + pop() on the same key can
        # silently lose an update (see task-4-report.md, Finding 2).
        self._lock = threading.Lock()

    def add(self, series_key: tuple, timestamp: int, value: float):
        minute_ts = timestamp - (timestamp % 60)
        key = (series_key, minute_ts)
        with self._lock:
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
        with self._lock:
            for key in list(self._buckets.keys()):
                series_key, minute_ts = key
                if minute_ts + 60 <= now:
                    total, mn, mx, n = self._buckets.pop(key)
                    ready.append((series_key, minute_ts, total / n, mn, mx, n))
        return ready

    def flush_all(self) -> list:
        """Return and remove every bucket regardless of elapsed time (shutdown path)."""
        ready = []
        with self._lock:
            for (series_key, minute_ts), (total, mn, mx, n) in self._buckets.items():
                ready.append((series_key, minute_ts, total / n, mn, mx, n))
            self._buckets.clear()
        return ready


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
    try:
        conn.executemany(
            'INSERT OR REPLACE INTO readings (series_id, ts, avg, min, max, n) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            rows)
        conn.execute('COMMIT')
    except Exception:
        conn.execute('ROLLBACK')
        raise


# ── Rollup and retention ─────────────────────────────────────────────────────
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
    try:
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
    except Exception:
        conn.execute('ROLLBACK')
        raise
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')


# ── MQTT history request/response (mirrors portal.py's /api/history) ────────
def _history_db_ro(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)


def _query_points(conn: sqlite3.Connection, kind, zone, metric: str, hours: float) -> dict:
    table = 'readings' if hours <= 48 else 'readings_hourly'
    resolution = 'minute' if table == 'readings' else 'hour'
    cutoff = int(time.time() - hours * 3600)
    row = conn.execute(
        'SELECT id FROM series WHERE kind=? AND zone IS ? AND metric=?',
        (kind, zone, metric)).fetchone()
    if row is None:
        return {'zone': zone, 'metric': metric, 'resolution': resolution, 'points': []}
    series_id = row[0]
    pts = conn.execute(
        f'SELECT ts, avg, min, max FROM {table} WHERE series_id=? AND ts >= ? ORDER BY ts',
        (series_id, cutoff)).fetchall()
    return {
        'zone': zone, 'metric': metric, 'resolution': resolution,
        'points': [[p[0], p[1], p[2], p[3]] for p in pts],
    }


def _handle_history_request(client, db_path: str, raw_payload: bytes) -> None:
    try:
        req = json.loads(raw_payload.decode())
        req_id = req['id']
    except Exception:
        return  # malformed request — nothing sane to respond to, no id to reply on
    try:
        conn = _history_db_ro(db_path)
        try:
            response = _query_points(
                conn, req.get('kind'), req.get('zone'), req.get('metric'),
                float(req.get('hours', 24)))
        finally:
            conn.close()
    except Exception as e:
        response = {'error': str(e)}
    client.publish(HISTORY_RESPONSE_PREFIX + req_id, json.dumps(response), qos=1, retain=False)


# ── Topic parsing ─────────────────────────────────────────────────────────────
_ZONE_METRIC_GROUPS = {'air', 'soil', 'light'}
_WEATHER_METRICS = {'temperature', 'humidity', 'wind_kmh', 'uv_index', 'rain_mm_1h', 'pressure'}


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


def _flush_tick(conn: sqlite3.Connection, series_ids: dict, buffer: MinuteBucketBuffer, now: int) -> None:
    """Write out buckets whose minute has fully elapsed.

    write_buckets() already rolls back and re-raises on failure (Task 2); if
    that were left uncaught here it would crash the whole recorder process
    over a transient error (e.g. "database is locked"). We log and move on
    instead — the batch that failed to write is lost, but per the design
    spec that's an accepted trade-off (losing at most a few minutes of
    buffered readings) versus taking the whole service down.
    """
    try:
        write_buckets(conn, series_ids, buffer.flush_ready(now))
    except Exception as e:
        print(f'[recorder] ERROR: write_buckets failed during flush, dropping this batch: {e}', flush=True)


def _rollup_tick(conn: sqlite3.Connection, now: int, raw_days: int, hourly_days: int) -> None:
    """Run hourly rollup + retention pruning, logging (not raising) on failure.

    Same rationale as _flush_tick: rollup_and_prune() rolls back and
    re-raises on failure (Task 3); a transient failure here must not crash
    the process. The next hourly tick will retry from the same watermark.
    """
    try:
        rollup_and_prune(conn, now, raw_days, hourly_days)
    except Exception as e:
        print(f'[recorder] ERROR: rollup_and_prune failed: {e}', flush=True)


def _flush_shutdown(conn: sqlite3.Connection, series_ids: dict, buffer: MinuteBucketBuffer) -> None:
    """Final flush of all remaining buckets (including in-progress minutes) on shutdown.

    Must not raise: a write failure here must not skip the cleanup
    (conn.close() / client.loop_stop() / client.disconnect()) that follows
    it in run().
    """
    try:
        write_buckets(conn, series_ids, buffer.flush_all())
    except Exception as e:
        print(f'[recorder] ERROR: write_buckets failed during shutdown flush, dropping remaining buckets: {e}', flush=True)


def run():
    cfg = load_config()
    conn = init_db(cfg['db_path'])
    buffer = MinuteBucketBuffer()
    series_ids = {}

    def on_connect(client, userdata, flags, rc, properties=None):
        for topic in SUBSCRIBE_TOPICS:
            client.subscribe(topic)
        client.subscribe(HISTORY_REQUEST_TOPIC)
        print(f'[recorder] Connected, subscribed to {len(SUBSCRIBE_TOPICS)} topic patterns '
              f'+ {HISTORY_REQUEST_TOPIC}', flush=True)

    def on_message(client, userdata, msg):
        if msg.topic == HISTORY_REQUEST_TOPIC:
            _handle_history_request(client, cfg['db_path'], msg.payload)
            return
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
            _flush_tick(conn, series_ids, buffer, int(now))
            last_flush = now
            if now - last_rollup >= 3600:
                _rollup_tick(conn, int(now), cfg['raw_days'], cfg['hourly_days'])
                last_rollup = now

    print('[recorder] Stopping, flushing remaining buckets...', flush=True)
    _flush_shutdown(conn, series_ids, buffer)
    conn.close()
    client.loop_stop()
    client.disconnect()
    print('[recorder] Stopped.', flush=True)


if __name__ == '__main__':
    run()
