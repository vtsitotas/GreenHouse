"""SQLite metadata store for camera motion events.

Deliberately holds no image bytes — the camera's own SD card is the photo
store (see the design spec). This just tracks which event_ids exist, when
they fired, and their diff score, so the Pi can answer "what are the recent
events" and "which events are old enough to prune" without asking the
camera every time.
"""
import os
import sqlite3

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS events (
  event_id   TEXT PRIMARY KEY,
  ts         INTEGER NOT NULL,
  diff_score REAL NOT NULL
);
'''


def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(_SCHEMA)
    return conn


def record_event(conn: sqlite3.Connection, event_id: str, ts: int, diff_score: float) -> None:
    conn.execute(
        'INSERT OR REPLACE INTO events (event_id, ts, diff_score) VALUES (?, ?, ?)',
        (event_id, ts, diff_score))
    conn.commit()


def expired_events(conn: sqlite3.Connection, now: int, max_age_days: int = 7) -> list[str]:
    cutoff = now - max_age_days * 86400
    rows = conn.execute('SELECT event_id FROM events WHERE ts < ?', (cutoff,)).fetchall()
    return [r[0] for r in rows]


def delete_event(conn: sqlite3.Connection, event_id: str) -> None:
    conn.execute('DELETE FROM events WHERE event_id = ?', (event_id,))
    conn.commit()


def latest_event(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        'SELECT event_id, ts FROM events ORDER BY ts DESC LIMIT 1').fetchone()
    return {'event_id': row[0], 'ts': row[1]} if row else None
