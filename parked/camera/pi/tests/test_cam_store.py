import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import cam_store


def test_init_db_creates_events_table(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'sub' / 'cam.db'))
    conn.execute('SELECT event_id, ts, diff_score FROM events')  # raises if missing
    conn.close()


def test_record_and_latest_event(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'cam.db'))
    cam_store.record_event(conn, 'evt1', 1000, 20.0)
    cam_store.record_event(conn, 'evt2', 2000, 30.0)
    assert cam_store.latest_event(conn) == {'event_id': 'evt2', 'ts': 2000}
    conn.close()


def test_latest_event_returns_none_when_empty(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'cam.db'))
    assert cam_store.latest_event(conn) is None
    conn.close()


def test_expired_events_returns_only_events_older_than_max_age(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'cam.db'))
    now = 1_000_000
    cam_store.record_event(conn, 'old', now - 8 * 86400, 15.0)
    cam_store.record_event(conn, 'recent', now - 1 * 86400, 15.0)
    assert cam_store.expired_events(conn, now, max_age_days=7) == ['old']
    conn.close()


def test_delete_event_removes_row(tmp_path):
    conn = cam_store.init_db(str(tmp_path / 'cam.db'))
    cam_store.record_event(conn, 'evt1', 1000, 20.0)
    cam_store.delete_event(conn, 'evt1')
    assert cam_store.latest_event(conn) is None
    conn.close()
