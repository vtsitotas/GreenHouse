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


if __name__ == '__main__':
    pass  # run() is added in Task 4
