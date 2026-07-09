# History Custom Date-Range Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user pick an arbitrary past date range (including a single day) in the History screen and see the real recorded data for it, over both the LAN (HTTP) and remote (MQTT/HiveMQ) paths, without disturbing any of the existing rolling-window (24h/7d/30d/90d) behavior.

**Architecture:** Extract the query logic currently duplicated between `portal.py`'s `/api/history` route and `recorder.py`'s MQTT-mirrored `_query_points()` into one shared `pi/shared/history_query.py::query_points()`, extended with an optional absolute `since`/`until` window alongside the existing relative `hours` window. Both backend transports (HTTP query params, MQTT request JSON) grow matching optional `since`/`until` fields. On the app side, `HistoryQuery` grows nullable `since`/`until` fields that — when both set — override `hours` end-to-end (`HistoryService.fetchPoints`, `GreenhouseRepository.fetchHistoryViaMqtt`, `historyPointsProvider`). `historyWithPredictionProvider` skips its forecast/trend overlay for custom ranges. `HistoryScreen` gains a 5th "Custom…" chip that opens `showDateRangePicker` and swaps the active query.

**Tech Stack:** Python 3 / Flask / paho-mqtt / sqlite3 (Pi side), Flutter 3.32.4 / Dart 3.8.1 / Riverpod 2.x (app side, no new dependencies — `showDateRangePicker` is built into `flutter/material.dart`), `pytest` (Pi tests), `flutter_test` + `mocktail` (app tests).

**Reference spec:** `docs/superpowers/specs/2026-07-09-history-custom-date-range-design.md`

## Global Constraints

- **Nothing that currently works may regress.** The rolling-window chips (24h/7d/30d/90d) and their `hours`-only query path must keep working byte-for-byte identically — both backend transports (HTTP `/api/history`, MQTT `greenhouse/history/request`) are already verified working on real hardware (LAN and HiveMQ Cloud remote). Every task below either extends an existing test unmodified or adds a new one alongside it; no existing test's assertions are changed, only appended to.
- The shared `query_points()` function's `hours`-only code path must be the exact same SQL/cutoff logic as the pre-refactor inline code in `portal.py`/`recorder.py` — no behavior change, only relocation. The `since`/`until` path is new code, exercised only when both are provided.
- No new Flutter dependency. `showDateRangePicker`/`DateTimeRange` come from `package:flutter/material.dart`, already imported everywhere that needs them.
- Testing is scoped proportionally to a thesis project (per project convention) — real regression coverage where it matters, no gold-plating (no stress tests, no production-grade concurrency hardening) beyond what's needed to trust this specific change.
- `recorder.py`'s `_handle_history_request` (the MQTT history responder) has **zero direct test coverage today** and is touched by this plan (Task 3). Since we're modifying it anyway, and it backs the already-working HiveMQ remote path, this plan adds its first direct tests rather than assuming it still works. `GreenhouseRepository.fetchHistoryViaMqtt` already has one existing test (`greenhouse_repository_test.dart`); Task 5 extends that file rather than assuming the new since/until behavior is covered by the existing test.

---

### Task 1: Shared `query_points()` — `pi/shared/history_query.py`

**Files:**
- Create: `pi/shared/history_query.py`
- Test: `pi/tests/test_history_query.py`

**Interfaces:**
- Produces: `query_points(conn, kind, zone, metric, *, hours=24, since=None, until=None) -> dict` — returns `{'zone': ..., 'metric': ..., 'resolution': 'minute'|'hour', 'points': [[ts, avg, min, max], ...]}`. When `since` and `until` (unix epoch seconds) are both given, they override `hours` and bound the query to `[since, until]` inclusive; otherwise `hours` selects a window ending at `time.time()`, identical to the pre-refactor behavior in `portal.py`/`recorder.py`.
- Consumes: a `sqlite3.Connection` already pointed at a DB created by `recorder.init_db()` (existing, `pi/scripts/recorder.py`).

- [ ] **Step 1: Write the failing tests**

Create `pi/tests/test_history_query.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && py -m pytest tests/test_history_query.py -v` (use `python3` in place of `py` on Linux/macOS)
Expected: FAIL — `pi/shared/history_query.py` does not exist yet.

- [ ] **Step 3: Implement `history_query.py`**

Create `pi/shared/history_query.py`:

```python
"""Shared history-query logic for the LAN HTTP endpoint (portal.py's
/api/history) and the MQTT request/response path (recorder.py's
_handle_history_request) -- both answer "give me points for (kind, zone,
metric) over some time window," either a relative window (hours back from
now) or an absolute one (since/until, unix epoch seconds). Kept in one place
so the two transports can't drift apart.
"""
import time


def query_points(conn, kind, zone, metric, *, hours=24, since=None, until=None):
    """Query recorded points for (kind, zone, metric).

    Pass since/until (unix epoch seconds) together for an absolute range --
    they take precedence over hours when both are given. Otherwise hours
    selects a window ending now, exactly as before this function existed.
    """
    if since is not None and until is not None:
        span_seconds = until - since
        cutoff = int(since)
        upper = int(until)
    else:
        span_seconds = hours * 3600
        cutoff = int(time.time() - span_seconds)
        upper = None

    table = 'readings' if span_seconds <= 48 * 3600 else 'readings_hourly'
    resolution = 'minute' if table == 'readings' else 'hour'

    row = conn.execute(
        'SELECT id FROM series WHERE kind=? AND zone IS ? AND metric=?',
        (kind, zone, metric)).fetchone()
    if row is None:
        return {'zone': zone, 'metric': metric, 'resolution': resolution, 'points': []}
    series_id = row[0]
    if upper is None:
        pts = conn.execute(
            f'SELECT ts, avg, min, max FROM {table} WHERE series_id=? AND ts >= ? ORDER BY ts',
            (series_id, cutoff)).fetchall()
    else:
        pts = conn.execute(
            f'SELECT ts, avg, min, max FROM {table} WHERE series_id=? AND ts >= ? AND ts <= ? '
            'ORDER BY ts',
            (series_id, cutoff, upper)).fetchall()
    return {
        'zone': zone, 'metric': metric, 'resolution': resolution,
        'points': [[p[0], p[1], p[2], p[3]] for p in pts],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && py -m pytest tests/test_history_query.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add pi/shared/history_query.py pi/tests/test_history_query.py
git commit -m "feat: add shared query_points() supporting absolute since/until ranges"
```

---

### Task 2: Wire `portal.py`'s `/api/history` to the shared query function

**Files:**
- Modify: `pi/portal/portal.py`
- Test: `pi/tests/test_portal_history.py`

**Interfaces:**
- Consumes: `query_points()` from Task 1 (`pi/shared/history_query.py`).
- Produces: `/api/history` gains optional `since`/`until` query params (unix epoch seconds as strings). Providing only one of the pair returns `400`. Providing neither behaves exactly as today (`hours`, default `24`).

- [ ] **Step 1: Write the failing tests**

Append to `pi/tests/test_portal_history.py` (after the existing `test_history_selects_hourly_table_beyond_48h`):

```python


def test_history_accepts_since_until_and_bounds_points(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    now = 100000
    _seed_db(db_path, now=now)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    client = portal.app.test_client()
    resp = client.get(
        f'/api/history?zone=zone1&metric=air_temperature&since={now - 90}&until={now - 30}')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['resolution'] == 'minute'
    assert len(data['points']) == 1
    assert data['points'][0][0] == now - 60


def test_history_since_until_overrides_hours(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    now = 100000
    _seed_db(db_path, now=now)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    client = portal.app.test_client()
    # hours=1 alone would select minute resolution; a >48h since/until span
    # must win when both are given.
    resp = client.get(
        '/api/history?zone=zone1&metric=air_temperature&hours=1'
        f'&since={now - 100 * 3600}&until={now}')
    data = json.loads(resp.data)
    assert data['resolution'] == 'hour'


def test_history_requires_since_and_until_together(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    _seed_db(db_path, now=100000)
    monkeypatch.setattr(portal, '_RECORDER_DB', db_path)
    client = portal.app.test_client()
    resp = client.get('/api/history?zone=zone1&metric=air_temperature&since=100')
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify the new ones fail (and the 5 existing ones still pass)**

Run: `cd pi && py -m pytest tests/test_portal_history.py -v`
Expected: the 5 pre-existing tests PASS unchanged; the 3 new tests FAIL (`/api/history` doesn't accept `since`/`until` yet).

- [ ] **Step 3: Add the shared-module import**

In `pi/portal/portal.py`, locate the top-of-file imports:

```python
import json
import os
import sqlite3
import subprocess
import time

from flask import Flask, abort, jsonify, redirect, render_template, request
```

Replace with:

```python
import json
import os
import sqlite3
import subprocess
import sys
import time

from flask import Flask, abort, jsonify, redirect, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
from history_query import query_points
```

- [ ] **Step 4: Rewrite the `history()` route**

Locate:

```python
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

Replace with:

```python
@app.route("/api/history")
def history():
    zone = request.args.get("zone")
    kind = request.args.get("kind") or ("zone" if zone else None)
    metric = request.args.get("metric")
    since_raw = request.args.get("since")
    until_raw = request.args.get("until")
    try:
        hours = float(request.args.get("hours", 24))
        since = float(since_raw) if since_raw is not None else None
        until = float(until_raw) if until_raw is not None else None
    except ValueError:
        return jsonify({"error": "hours/since/until must be numbers"}), 400
    if not metric or not kind:
        return jsonify({"error": "metric and (zone or kind) are required"}), 400
    if (since is None) != (until is None):
        return jsonify({"error": "since and until must be provided together"}), 400

    try:
        conn = _history_db()
        result = query_points(conn, kind, zone, metric, hours=hours, since=since, until=until)
        conn.close()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd pi && py -m pytest tests/test_portal_history.py -v`
Expected: PASS (8 tests total — 5 pre-existing + 3 new)

- [ ] **Step 6: Run the full Pi test suite to confirm nothing else regressed**

Run: `cd pi && py -m pytest tests/ -q`
Expected: PASS (all tests across `test_recorder.py`, `test_portal_history.py`, `test_weather_rules.py`, `test_history_query.py`)

- [ ] **Step 7: Commit**

```bash
git add pi/portal/portal.py pi/tests/test_portal_history.py
git commit -m "feat: accept since/until on /api/history via the shared query_points()"
```

---

### Task 3: Wire `recorder.py`'s MQTT history handler to the shared query function

**Files:**
- Modify: `pi/scripts/recorder.py`
- Test: `pi/tests/test_recorder.py`

**Interfaces:**
- Consumes: `query_points()` from Task 1.
- Produces: `greenhouse/history/request` payloads may now include optional `since`/`until` (unix epoch seconds), handled the same way as the HTTP endpoint. `_query_points()` (the old inline duplicate) is removed from `recorder.py`; `_handle_history_request()` now calls the shared `query_points()`.
- **Fills an existing test gap:** `_handle_history_request` has no direct test today. This task adds one, using a minimal fake MQTT client (records `publish()` calls) rather than a real broker — proportional to a thesis project, no live-broker test needed (see project convention).

- [ ] **Step 1: Write the failing tests**

In `pi/tests/test_recorder.py`, add this import near the top (alongside the existing `import recorder`):

```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import history_query
```

Then append these tests at the end of the file:

```python


# ── MQTT history request/response (now backed by the shared query_points) ──
class _FakeMqttClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def _seed_history_db(db_path, now):
    conn = recorder.init_db(db_path)
    series_ids = {}
    recorder.write_buckets(conn, series_ids, [
        (('zone', 'zone1', 'air_temperature'), now - 60, 22.0, 21.0, 23.0, 2),
    ])
    return conn


def test_handle_history_request_hours_mode_publishes_points(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'test.db')
    now = 100000
    _seed_history_db(db_path, now).close()
    monkeypatch.setattr(history_query.time, 'time', lambda: now)
    client = _FakeMqttClient()
    payload = json.dumps({'id': 'req1', 'kind': 'zone', 'zone': 'zone1',
                           'metric': 'air_temperature', 'hours': 1}).encode()
    recorder._handle_history_request(client, db_path, payload)
    assert len(client.published) == 1
    topic, body, qos, retain = client.published[0]
    assert topic == 'greenhouse/history/response/req1'
    assert retain is False
    data = json.loads(body)
    assert len(data['points']) == 1


def test_handle_history_request_since_until_mode_bounds_points(tmp_path):
    db_path = str(tmp_path / 'test.db')
    now = 100000
    _seed_history_db(db_path, now).close()
    client = _FakeMqttClient()
    payload = json.dumps({
        'id': 'req2', 'kind': 'zone', 'zone': 'zone1', 'metric': 'air_temperature',
        'since': now - 90, 'until': now - 30,
    }).encode()
    recorder._handle_history_request(client, db_path, payload)
    data = json.loads(client.published[0][1])
    assert len(data['points']) == 1
    assert data['points'][0][0] == now - 60


def test_handle_history_request_malformed_payload_does_not_raise(tmp_path):
    db_path = str(tmp_path / 'test.db')
    client = _FakeMqttClient()
    recorder._handle_history_request(client, db_path, b'not json')  # must not raise
    assert client.published == []  # no id to reply on -- nothing published


def test_handle_history_request_query_error_publishes_error_payload(tmp_path):
    db_path = str(tmp_path / 'test.db')  # never created -- query will fail
    client = _FakeMqttClient()
    payload = json.dumps({'id': 'req3', 'kind': 'zone', 'zone': 'zone1',
                           'metric': 'air_temperature'}).encode()
    recorder._handle_history_request(client, db_path, payload)
    data = json.loads(client.published[0][1])
    assert 'error' in data
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd pi && py -m pytest tests/test_recorder.py -v -k history_request`
Expected: `test_handle_history_request_since_until_mode_bounds_points` FAILS — today's `_handle_history_request` only reads `hours` from the request and ignores `since`/`until` entirely, so it queries the real last-24-hours window instead of the requested `[since, until]` bounds and returns 0 points instead of 1. `test_handle_history_request_hours_mode_publishes_points`, `test_handle_history_request_malformed_payload_does_not_raise`, and `test_handle_history_request_query_error_publishes_error_payload` already PASS against today's code (they don't exercise `since`/`until`) — that's fine, they still validate real behavior and will keep passing after Step 4's rewire.

- [ ] **Step 3: Add the shared-module import and remove the old inline query function**

In `pi/scripts/recorder.py`, locate the top-of-file imports:

```python
import json
import os
import signal
import sqlite3
import threading
import time

import paho.mqtt.client as mqtt
```

Replace with:

```python
import json
import os
import signal
import sqlite3
import sys
import threading
import time

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
from history_query import query_points
```

Locate and delete the old inline query function entirely (keep `_history_db_ro` just above it):

```python
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
```

- [ ] **Step 4: Rewrite `_handle_history_request` to use the shared function**

Locate:

```python
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
```

Replace with:

```python
def _handle_history_request(client, db_path: str, raw_payload: bytes) -> None:
    try:
        req = json.loads(raw_payload.decode())
        req_id = req['id']
    except Exception:
        return  # malformed request — nothing sane to respond to, no id to reply on
    try:
        conn = _history_db_ro(db_path)
        try:
            since = req.get('since')
            until = req.get('until')
            response = query_points(
                conn, req.get('kind'), req.get('zone'), req.get('metric'),
                hours=float(req.get('hours', 24)),
                since=float(since) if since is not None else None,
                until=float(until) if until is not None else None)
        finally:
            conn.close()
    except Exception as e:
        response = {'error': str(e)}
    client.publish(HISTORY_RESPONSE_PREFIX + req_id, json.dumps(response), qos=1, retain=False)
```

Also update the module docstring-comment just above `_history_db_ro` (was `# ── MQTT history request/response (mirrors portal.py's /api/history) ──`) to reflect that it no longer mirrors a copy, it shares one:

```python
# ── MQTT history request/response (shared query logic with portal.py) ──────
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd pi && py -m pytest tests/test_recorder.py -v`
Expected: PASS (all pre-existing `test_recorder.py` tests plus the 4 new `history_request` tests)

- [ ] **Step 6: Run the full Pi test suite to confirm nothing else regressed**

Run: `cd pi && py -m pytest tests/ -q`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add pi/scripts/recorder.py pi/tests/test_recorder.py
git commit -m "feat: back the MQTT history responder with the shared query_points(), add its first tests"
```

---

### Task 4: `HistoryService.fetchPoints` gains `since`/`until`

**Files:**
- Modify: `app/lib/services/history_service.dart`
- Test: `app/test/services/history_service_test.dart`

**Interfaces:**
- Produces: `fetchPoints({required lanHost, zone, kind, required metric, hours = 24, DateTime? since, DateTime? until})`. When `since`/`until` are both non-null, the HTTP request sends `since`/`until` (unix epoch seconds) instead of `hours`.

- [ ] **Step 1: Write the failing tests**

Append to `app/test/services/history_service_test.dart` (inside the existing `group('HistoryService', ...)`, after `fetchPoints throws on non-200 response`):

```dart

    test('fetchPoints sends since/until instead of hours for a custom range', () async {
      final client = MockClient((request) async {
        expect(request.url.queryParameters['since'], '1000');
        expect(request.url.queryParameters['until'], '2000');
        expect(request.url.queryParameters.containsKey('hours'), isFalse);
        return http.Response(jsonEncode({'points': <List<dynamic>>[]}), 200);
      });
      final service = HistoryService(client: client);
      await service.fetchPoints(
        lanHost: 'greenhouse.local',
        zone: 'zone1',
        metric: 'air_temperature',
        since: DateTime.fromMillisecondsSinceEpoch(1000000),
        until: DateTime.fromMillisecondsSinceEpoch(2000000),
      );
    });

    test('fetchPoints sends hours (not since/until) when no custom range is given', () async {
      final client = MockClient((request) async {
        expect(request.url.queryParameters['hours'], '24');
        expect(request.url.queryParameters.containsKey('since'), isFalse);
        expect(request.url.queryParameters.containsKey('until'), isFalse);
        return http.Response(jsonEncode({'points': <List<dynamic>>[]}), 200);
      });
      final service = HistoryService(client: client);
      await service.fetchPoints(lanHost: 'greenhouse.local', zone: 'zone1', metric: 'x');
    });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/services/history_service_test.dart`
Expected: the whole file fails to compile — `fetchPoints` doesn't have `since`/`until` parameters yet, so the 2 new tests' calls are a compile error. This is expected; the 3 pre-existing tests aren't actually broken, they just can't run until Step 3 adds the parameters and the file compiles again.

- [ ] **Step 3: Update `fetchPoints`**

In `app/lib/services/history_service.dart`, locate:

```dart
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
```

Replace with:

```dart
  Future<List<HistoryPoint>> fetchPoints({
    required String lanHost,
    String? zone,
    String? kind,
    required String metric,
    double hours = 24,
    DateTime? since,
    DateTime? until,
  }) async {
    final isCustomRange = since != null && until != null;
    final params = <String, String>{
      'metric': metric,
      if (zone != null) 'zone': zone,
      if (kind != null) 'kind': kind,
      if (isCustomRange) 'since': (since.millisecondsSinceEpoch ~/ 1000).toString(),
      if (isCustomRange) 'until': (until.millisecondsSinceEpoch ~/ 1000).toString(),
      if (!isCustomRange) 'hours': hours.toString(),
    };
    final uri = Uri.http(lanHost, '/api/history', params);
```

(The rest of the method body is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/services/history_service_test.dart`
Expected: PASS (5 tests total)

- [ ] **Step 5: Commit**

```bash
git add app/lib/services/history_service.dart app/test/services/history_service_test.dart
git commit -m "feat: HistoryService.fetchPoints accepts an absolute since/until range"
```

---

### Task 5: `GreenhouseRepository.fetchHistoryViaMqtt` gains `since`/`until`

**Files:**
- Modify: `app/lib/repository/greenhouse_repository.dart`
- Test: `app/test/repository/greenhouse_repository_test.dart`

**Interfaces:**
- Produces: `fetchHistoryViaMqtt({zone, kind, required metric, hours = 24, DateTime? since, DateTime? until})`. When `since`/`until` are both non-null, the MQTT request payload includes `since`/`until` (unix epoch seconds) instead of `hours`.

- [ ] **Step 1: Write the failing tests**

Append to `app/test/repository/greenhouse_repository_test.dart` (inside `main()`, after the existing `fetchHistoryViaMqtt requests over MQTT and resolves the matching response` test):

```dart

  test('fetchHistoryViaMqtt sends since/until instead of hours for a custom range', () async {
    repo.connect(_config);

    final resultFuture = repo.fetchHistoryViaMqtt(
      zone: 'zone1',
      kind: 'zone',
      metric: 'air_temperature',
      since: DateTime.fromMillisecondsSinceEpoch(1000000),
      until: DateTime.fromMillisecondsSinceEpoch(2000000),
    );

    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/history/request', captureAny(), retain: any(named: 'retain')),
    ).captured.single as String;
    final request = jsonDecode(capturedPayload) as Map<String, dynamic>;
    expect(request['since'], 1000);
    expect(request['until'], 2000);
    expect(request.containsKey('hours'), isFalse);

    eventsCtrl.add(HistoryResponseRaw(request['id'] as String, jsonEncode({'points': <List<dynamic>>[]})));
    await resultFuture;
  });

  test('fetchHistoryViaMqtt still sends hours (not since/until) for a normal rolling window', () async {
    repo.connect(_config);

    final resultFuture = repo.fetchHistoryViaMqtt(
        zone: 'zone1', kind: 'zone', metric: 'air_temperature', hours: 168);

    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/history/request', captureAny(), retain: any(named: 'retain')),
    ).captured.single as String;
    final request = jsonDecode(capturedPayload) as Map<String, dynamic>;
    expect(request['hours'], 168);
    expect(request.containsKey('since'), isFalse);
    expect(request.containsKey('until'), isFalse);

    eventsCtrl.add(HistoryResponseRaw(request['id'] as String, jsonEncode({'points': <List<dynamic>>[]})));
    await resultFuture;
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/repository/greenhouse_repository_test.dart`
Expected: the whole file fails to compile — `fetchHistoryViaMqtt` doesn't have `since`/`until` parameters yet, so the 2 new tests' calls are a compile error. This is expected; the pre-existing tests aren't actually broken, they just can't run until Step 3 adds the parameters and the file compiles again.

- [ ] **Step 3: Update `fetchHistoryViaMqtt`**

In `app/lib/repository/greenhouse_repository.dart`, locate:

```dart
  Future<Map<String, dynamic>?> fetchHistoryViaMqtt({
    String? zone,
    String? kind,
    required String metric,
    double hours = 24,
  }) async {
    final id = 'h${DateTime.now().microsecondsSinceEpoch}';
    final payload = jsonEncode({
      'id': id,
      'type': 'points',
      if (zone != null) 'zone': zone,
      if (kind != null) 'kind': kind,
      'metric': metric,
      'hours': hours,
    });
```

Replace with:

```dart
  Future<Map<String, dynamic>?> fetchHistoryViaMqtt({
    String? zone,
    String? kind,
    required String metric,
    double hours = 24,
    DateTime? since,
    DateTime? until,
  }) async {
    final isCustomRange = since != null && until != null;
    final id = 'h${DateTime.now().microsecondsSinceEpoch}';
    final payload = jsonEncode({
      'id': id,
      'type': 'points',
      if (zone != null) 'zone': zone,
      if (kind != null) 'kind': kind,
      'metric': metric,
      if (isCustomRange) 'since': since.millisecondsSinceEpoch ~/ 1000,
      if (isCustomRange) 'until': until.millisecondsSinceEpoch ~/ 1000,
      if (!isCustomRange) 'hours': hours,
    });
```

(The rest of the method body is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/repository/greenhouse_repository_test.dart`
Expected: PASS (all pre-existing tests plus the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add app/lib/repository/greenhouse_repository.dart app/test/repository/greenhouse_repository_test.dart
git commit -m "feat: GreenhouseRepository.fetchHistoryViaMqtt accepts an absolute since/until range"
```

---

### Task 6: `HistoryQuery` gains `since`/`until`; `historyPointsProvider` forwards them

**Files:**
- Modify: `app/lib/providers/history_provider.dart`
- Test: `app/test/providers/history_provider_test.dart`

**Interfaces:**
- Consumes: `HistoryService.fetchPoints` (Task 4), `GreenhouseRepository.fetchHistoryViaMqtt` (Task 5) — both already accept `since`/`until`.
- Produces: `HistoryQuery({zone, kind, required metric, hours = 24, DateTime? since, DateTime? until})`, `==`/`hashCode` covering all six fields, and `bool get isCustomRange => since != null && until != null`. `historyPointsProvider` forwards `query.since`/`query.until` to whichever backend it picks (HTTP or MQTT), same branch logic as today.

- [ ] **Step 1: Write the failing tests**

Append to `app/test/providers/history_provider_test.dart` (after the existing `HistoryQuery equality considers zone, kind, metric, and hours` test):

```dart

  test('HistoryQuery.isCustomRange is true only when both since and until are set', () {
    final since = DateTime.fromMillisecondsSinceEpoch(0);
    final until = DateTime.fromMillisecondsSinceEpoch(1000);
    expect(const HistoryQuery(metric: 'temperature').isCustomRange, isFalse);
    expect(HistoryQuery(metric: 'temperature', since: since).isCustomRange, isFalse);
    expect(HistoryQuery(metric: 'temperature', since: since, until: until).isCustomRange, isTrue);
  });

  test('HistoryQuery equality also considers since and until', () {
    final since = DateTime.fromMillisecondsSinceEpoch(0);
    final until = DateTime.fromMillisecondsSinceEpoch(1000);
    final a = HistoryQuery(metric: 'temperature', since: since, until: until);
    final b = HistoryQuery(metric: 'temperature', since: since, until: until);
    final c = HistoryQuery(metric: 'temperature', since: since, until: until.add(const Duration(seconds: 1)));
    expect(a, b);
    expect(a == c, isFalse);
  });

  test('historyPointsProvider forwards since/until to HistoryService for a custom range', () async {
    final mockService = MockHistoryService();
    final mockPairing = MockPairingService();
    when(() => mockPairing.loadConfig()).thenAnswer((_) async => const ConnectionConfig(
          lanHost: 'greenhouse.local',
          remoteHost: '',
          port: 8883,
          tlsFingerprint: '',
          username: '',
          password: '',
          remoteUsername: '',
          remotePassword: '',
        ));
    when(() => mockService.fetchPoints(
          lanHost: any(named: 'lanHost'),
          zone: any(named: 'zone'),
          kind: any(named: 'kind'),
          metric: any(named: 'metric'),
          hours: any(named: 'hours'),
          since: any(named: 'since'),
          until: any(named: 'until'),
        )).thenAnswer((_) async => []);

    final container = ProviderContainer(overrides: [
      historyServiceProvider.overrideWithValue(mockService),
      pairingServiceProvider.overrideWithValue(mockPairing),
    ]);
    addTearDown(container.dispose);

    final since = DateTime.fromMillisecondsSinceEpoch(1000000);
    final until = DateTime.fromMillisecondsSinceEpoch(2000000);
    final query = HistoryQuery(zone: 'zone1', metric: 'air_temperature', since: since, until: until);
    await container.read(historyPointsProvider(query).future);

    verify(() => mockService.fetchPoints(
          lanHost: 'greenhouse.local',
          zone: 'zone1',
          kind: 'zone',
          metric: 'air_temperature',
          hours: 24,
          since: since,
          until: until,
        )).called(1);
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/providers/history_provider_test.dart`
Expected: the whole file fails to compile — `HistoryQuery` doesn't have `since`/`until` constructor params or an `isCustomRange` getter yet (the mock's `fetchPoints` stub already accepts `since`/`until` since Task 4 landed first). This is expected; the pre-existing tests aren't actually broken, they just can't run until Step 3 adds `since`/`until`/`isCustomRange` to `HistoryQuery` and the file compiles again.

- [ ] **Step 3: Update `HistoryQuery` and `historyPointsProvider`**

In `app/lib/providers/history_provider.dart`, locate:

```dart
class HistoryQuery {
  final String? zone;
  final String kind;
  final String metric;
  final double hours;

  const HistoryQuery({
    this.zone,
    String? kind,
    required this.metric,
    this.hours = 24,
  }) : kind = kind ?? (zone != null ? 'zone' : 'weather');

  @override
  bool operator ==(Object other) =>
      other is HistoryQuery &&
      other.zone == zone &&
      other.kind == kind &&
      other.metric == metric &&
      other.hours == hours;

  @override
  int get hashCode => Object.hash(zone, kind, metric, hours);
}

final historyPointsProvider =
    FutureProvider.family<List<HistoryPoint>, HistoryQuery>((ref, query) async {
  final config = await ref.read(pairingServiceProvider).loadConfig();
  if (config == null) return [];

  // The HTTP /api/history endpoint only exists on the LAN — HiveMQ Cloud
  // only bridges MQTT, not HTTP. When connected remotely, fetch history via
  // an MQTT request/response round-trip instead.
  final status = ref.watch(connectionStatusProvider).valueOrNull;
  if (status == ConnectionStatus.remote) {
    final data = await ref.read(repositoryProvider).fetchHistoryViaMqtt(
          zone: query.zone,
          kind: query.kind,
          metric: query.metric,
          hours: query.hours,
        );
    if (data == null || data['error'] != null) {
      throw Exception('History fetch failed via MQTT');
    }
    return HistoryService.parsePoints(data);
  }

  final service = ref.read(historyServiceProvider);
  return service.fetchPoints(
    lanHost: config.lanHost,
    zone: query.zone,
    kind: query.kind,
    metric: query.metric,
    hours: query.hours,
  );
});
```

Replace with:

```dart
class HistoryQuery {
  final String? zone;
  final String kind;
  final String metric;
  final double hours;
  final DateTime? since;
  final DateTime? until;

  const HistoryQuery({
    this.zone,
    String? kind,
    required this.metric,
    this.hours = 24,
    this.since,
    this.until,
  }) : kind = kind ?? (zone != null ? 'zone' : 'weather');

  /// True when this query is an absolute custom range (since+until), rather
  /// than the rolling `hours`-back-from-now window.
  bool get isCustomRange => since != null && until != null;

  @override
  bool operator ==(Object other) =>
      other is HistoryQuery &&
      other.zone == zone &&
      other.kind == kind &&
      other.metric == metric &&
      other.hours == hours &&
      other.since == since &&
      other.until == until;

  @override
  int get hashCode => Object.hash(zone, kind, metric, hours, since, until);
}

final historyPointsProvider =
    FutureProvider.family<List<HistoryPoint>, HistoryQuery>((ref, query) async {
  final config = await ref.read(pairingServiceProvider).loadConfig();
  if (config == null) return [];

  // The HTTP /api/history endpoint only exists on the LAN — HiveMQ Cloud
  // only bridges MQTT, not HTTP. When connected remotely, fetch history via
  // an MQTT request/response round-trip instead.
  final status = ref.watch(connectionStatusProvider).valueOrNull;
  if (status == ConnectionStatus.remote) {
    final data = await ref.read(repositoryProvider).fetchHistoryViaMqtt(
          zone: query.zone,
          kind: query.kind,
          metric: query.metric,
          hours: query.hours,
          since: query.since,
          until: query.until,
        );
    if (data == null || data['error'] != null) {
      throw Exception('History fetch failed via MQTT');
    }
    return HistoryService.parsePoints(data);
  }

  final service = ref.read(historyServiceProvider);
  return service.fetchPoints(
    lanHost: config.lanHost,
    zone: query.zone,
    kind: query.kind,
    metric: query.metric,
    hours: query.hours,
    since: query.since,
    until: query.until,
  );
});
```

(`historyWithPredictionProvider` below this in the same file is untouched in this task — see Task 7.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/providers/history_provider_test.dart`
Expected: PASS (all pre-existing tests plus the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add app/lib/providers/history_provider.dart app/test/providers/history_provider_test.dart
git commit -m "feat: HistoryQuery gains since/until, historyPointsProvider forwards them"
```

---

### Task 7: `historyWithPredictionProvider` skips prediction for custom ranges

**Files:**
- Modify: `app/lib/providers/history_provider.dart`
- Test: `app/test/providers/history_provider_test.dart`

**Interfaces:**
- Consumes: `HistoryQuery.isCustomRange` (Task 6).
- Produces: `historyWithPredictionProvider` returns `predicted: []` whenever `query.isCustomRange` is true, regardless of how many actual points are loaded or whether a forecast is available — same public type/name/shape as today (`HistoryData`).

- [ ] **Step 1: Write the failing test**

Append to `app/test/providers/history_provider_test.dart`, inside the existing `group('historyWithPredictionProvider', ...)` (after `falls back to trend extrapolation for zone metrics (no forecast)`):

```dart

    test('never predicts for a custom (since/until) range, even with enough points', () async {
      final since = DateTime.fromMillisecondsSinceEpoch(0);
      final until = DateTime.fromMillisecondsSinceEpoch(120000);
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20), pt(60, 22), pt(120, 24)]),
      ]);
      addTearDown(container.dispose);
      final query = HistoryQuery(zone: 'zone1', metric: 'air_temperature', since: since, until: until);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.actual.length, 3);
      expect(data.predicted, isEmpty);
    });

    test('still predicts for a normal rolling-window query with enough points (regression check)',
        () async {
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20), pt(60, 22)]),
      ]);
      addTearDown(container.dispose);
      const query = HistoryQuery(zone: 'zone1', metric: 'soil_moisture', hours: 24);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.predicted, isNotEmpty);
    });
```

- [ ] **Step 2: Run tests to verify the new ones behave as expected**

Run: `cd app && flutter test test/providers/history_provider_test.dart`
Expected: the "never predicts for a custom range" test FAILS (today's code still predicts regardless of range type); the "still predicts for a normal rolling-window query" regression-check test already PASSES against the current code (it's asserting existing behavior, not new behavior) — confirm it passes both now and after Step 3.

- [ ] **Step 3: Skip prediction for custom ranges**

In `app/lib/providers/history_provider.dart`, locate:

```dart
final historyWithPredictionProvider =
    FutureProvider.family<HistoryData, HistoryQuery>((ref, query) async {
  final actual = await ref.watch(historyPointsProvider(query).future);
  if (actual.length < 2) {
    return (actual: actual, predicted: <HistoryPoint>[]);
  }
```

Replace with:

```dart
final historyWithPredictionProvider =
    FutureProvider.family<HistoryData, HistoryQuery>((ref, query) async {
  final actual = await ref.watch(historyPointsProvider(query).future);
  // Prediction extrapolates forward from "now" — meaningless for a custom
  // past date range, since what happened next is already known data, not a
  // forecast. Custom ranges show only the real recorded points.
  if (query.isCustomRange || actual.length < 2) {
    return (actual: actual, predicted: <HistoryPoint>[]);
  }
```

(Nothing else in the function changes.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/providers/history_provider_test.dart`
Expected: PASS (all tests in the file, including both the new custom-range test and the regression-check test)

- [ ] **Step 5: Commit**

```bash
git add app/lib/providers/history_provider.dart app/test/providers/history_provider_test.dart
git commit -m "feat: skip prediction overlay for custom date-range queries"
```

---

### Task 8: `HistoryScreen` — "Custom…" chip with a date-range picker

**Files:**
- Modify: `app/lib/screens/history/history_screen.dart`
- Test: `app/test/widgets/history_screen_test.dart`

**Interfaces:**
- Consumes: `HistoryQuery` (with `since`/`until`, Task 6), `historyWithPredictionProvider` (Task 7).
- Produces: `HistoryScreen` — same public constructor as today (`{required String zone, required String metric}`). Internally adds a 5th range chip, "Custom…", that opens `showDateRangePicker` (bounded `firstDate = now - 730 days`, `lastDate = now`) and switches the active query to a `since`/`until` range. The chip's own label reflects the picked range once set. `_rangeLabel`/`_axisTimeLabel` key off the effective span (custom range's real span, or `_hours` otherwise) instead of `_hours` alone.

- [ ] **Step 1: Write the failing tests**

Append to `app/test/widgets/history_screen_test.dart` (after the existing `weather sentinel zone shows weather metric tabs` test, inside `main()`). No new import is needed — the file already imports `package:greenhouse_app/providers/history_provider.dart` for `historyWithPredictionProvider`, which also exposes `HistoryQuery`.

```dart

  testWidgets('Custom chip opens a date-range picker and switches to a since/until query',
      (tester) async {
    HistoryQuery? lastQuery;
    await tester.pumpWidget(ProviderScope(
      overrides: [
        historyWithPredictionProvider.overrideWith((ref, query) async {
          lastQuery = query;
          return (actual: [_pt(0, 20.0), _pt(60, 21.0)], predicted: <HistoryPoint>[]);
        }),
      ],
      child: const MaterialApp(home: HistoryScreen(zone: 'zone1', metric: 'air_temperature')),
    ));
    await tester.pumpAndSettle();
    expect(lastQuery!.isCustomRange, isFalse);
    expect(find.text('Last 24 Hours'), findsOneWidget);

    await tester.tap(find.text('Custom…'));
    await tester.pumpAndSettle();
    // The range picker opens pre-populated with today→today (see Step 3),
    // so tapping Save immediately confirms a valid single-day range without
    // needing to navigate the calendar grid.
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();

    expect(lastQuery!.isCustomRange, isTrue);
    expect(find.text('Last 24 Hours'), findsNothing);
    expect(find.text('now'), findsNothing); // header no longer claims a past date is "now"
  });
```

- [ ] **Step 2: Run tests to verify the new one fails (and existing ones still pass)**

Run: `cd app && flutter test test/widgets/history_screen_test.dart`
Expected: the 6 pre-existing tests PASS unchanged; the new test FAILS (no "Custom…" chip exists yet).

- [ ] **Step 3: Add custom-range state, a picker, and the 5th chip**

In `app/lib/screens/history/history_screen.dart`, locate the date/time helper block just below the imports:

```dart
const _weekdayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _monthNames = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
];
String _twoDigits(int n) => n.toString().padLeft(2, '0');
String _timeLabel(DateTime t) => '${_twoDigits(t.hour)}:${_twoDigits(t.minute)}';
```

Replace with (adds `_dateLabel`, reused by the custom-range label and the axis formatter):

```dart
const _weekdayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _monthNames = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
];
String _twoDigits(int n) => n.toString().padLeft(2, '0');
String _timeLabel(DateTime t) => '${_twoDigits(t.hour)}:${_twoDigits(t.minute)}';
String _dateLabel(DateTime t) => '${_monthNames[t.month - 1]} ${t.day}';
```

Locate the state fields and `initState`:

```dart
class _HistoryScreenState extends ConsumerState<HistoryScreen> {
  late final String _kind;
  late final String? _zone;
  late String _metric;
  double _hours = 24;

  @override
  void initState() {
    super.initState();
    _kind = widget.zone == 'weather' ? 'weather' : 'zone';
    _zone = _kind == 'weather' ? null : widget.zone;
    _metric = widget.metric;
  }
```

Replace with:

```dart
class _HistoryScreenState extends ConsumerState<HistoryScreen> {
  late final String _kind;
  late final String? _zone;
  late String _metric;
  double _hours = 24;
  bool _customSelected = false;
  DateTime? _since;
  DateTime? _until;

  @override
  void initState() {
    super.initState();
    _kind = widget.zone == 'weather' ? 'weather' : 'zone';
    _zone = _kind == 'weather' ? null : widget.zone;
    _metric = widget.metric;
  }
```

Locate `_rangeLabel` and `_axisTimeLabel`:

```dart
  String get _rangeLabel {
    final tag = _ranges.firstWhere((r) => r.$1 == _hours, orElse: () => (_hours, '')).$2;
    return switch (tag) {
      '24h' => 'Last 24 Hours',
      '7d' => 'Last 7 Days',
      '30d' => 'Last 30 Days',
      '90d' => 'Last 90 Days',
      _ => 'Last ${_hours.round()} Hours',
    };
  }

  String _axisTimeLabel(DateTime t) {
    if (_hours <= 24) return _timeLabel(t);
    if (_hours <= 168) return '${_weekdayNames[t.weekday - 1]} ${_timeLabel(t)}';
    return '${_monthNames[t.month - 1]} ${t.day}';
  }
```

Replace with:

```dart
  String _customRangeLabel(DateTime since, DateTime until) {
    final sameDay =
        since.year == until.year && since.month == until.month && since.day == until.day;
    return sameDay ? _dateLabel(since) : '${_dateLabel(since)} – ${_dateLabel(until)}';
  }

  String get _customChipLabel =>
      (_since != null && _until != null) ? _customRangeLabel(_since!, _until!) : 'Custom…';

  /// Span this query effectively covers, in hours -- `_hours` for a rolling
  /// window, or the picked custom range's real span otherwise. Drives the
  /// same label/axis-formatting thresholds `_hours` alone used to.
  double get _effectiveHours => (_customSelected && _since != null && _until != null)
      ? _until!.difference(_since!).inSeconds / 3600.0
      : _hours;

  String get _rangeLabel {
    if (_customSelected && _since != null && _until != null) {
      return _customRangeLabel(_since!, _until!);
    }
    final tag = _ranges.firstWhere((r) => r.$1 == _hours, orElse: () => (_hours, '')).$2;
    return switch (tag) {
      '24h' => 'Last 24 Hours',
      '7d' => 'Last 7 Days',
      '30d' => 'Last 30 Days',
      '90d' => 'Last 90 Days',
      _ => 'Last ${_hours.round()} Hours',
    };
  }

  String _axisTimeLabel(DateTime t) {
    final h = _effectiveHours;
    if (h <= 24) return _timeLabel(t);
    if (h <= 168) return '${_weekdayNames[t.weekday - 1]} ${_timeLabel(t)}';
    return _dateLabel(t);
  }

  Future<void> _pickCustomRange() async {
    final now = DateTime.now();
    // Defaults to today->today so "Save" is immediately actionable without
    // navigating the calendar grid first (also makes this flow easy to
    // widget-test deterministically).
    final initial = (_since != null && _until != null)
        ? DateTimeRange(start: _since!, end: _until!)
        : DateTimeRange(start: now, end: now);
    final picked = await showDateRangePicker(
      context: context,
      firstDate: now.subtract(const Duration(days: 730)),
      lastDate: now,
      initialDateRange: initial,
    );
    if (picked == null) return;
    setState(() {
      _customSelected = true;
      _since = DateTime(picked.start.year, picked.start.month, picked.start.day);
      _until = DateTime(picked.end.year, picked.end.month, picked.end.day, 23, 59, 59);
    });
  }
```

Locate the `build` method's query construction and the range-chip `Wrap`:

```dart
  @override
  Widget build(BuildContext context) {
    final query = HistoryQuery(zone: _zone, kind: _kind, metric: _metric, hours: _hours);
    final dataAsync = ref.watch(historyWithPredictionProvider(query));
    final unit = _unitFor(_metric);
```

Replace with:

```dart
  @override
  Widget build(BuildContext context) {
    final query = (_customSelected && _since != null && _until != null)
        ? HistoryQuery(zone: _zone, kind: _kind, metric: _metric, since: _since, until: _until)
        : HistoryQuery(zone: _zone, kind: _kind, metric: _metric, hours: _hours);
    final dataAsync = ref.watch(historyWithPredictionProvider(query));
    final unit = _unitFor(_metric);
```

Locate the range-chip row:

```dart
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Wrap(
              spacing: 8,
              children: _ranges
                  .map((r) => ChoiceChip(
                        label: Text(r.$2),
                        selected: r.$1 == _hours,
                        onSelected: (_) => setState(() => _hours = r.$1),
                      ))
                  .toList(),
            ),
          ),
```

Replace with:

```dart
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Wrap(
              spacing: 8,
              children: [
                ..._ranges.map((r) => ChoiceChip(
                      label: Text(r.$2),
                      selected: !_customSelected && r.$1 == _hours,
                      onSelected: (_) => setState(() {
                        _customSelected = false;
                        _hours = r.$1;
                      }),
                    )),
                ChoiceChip(
                  label: Text(_customChipLabel),
                  selected: _customSelected,
                  onSelected: (_) => _pickCustomRange(),
                ),
              ],
            ),
          ),
```

Finally, locate the "now" label next to the current value (it would otherwise misleadingly say "now" while showing a past custom date's reading):

```dart
                              const SizedBox(width: 8),
                              Text('now', style: Theme.of(context).textTheme.bodySmall),
```

Replace with:

```dart
                              const SizedBox(width: 8),
                              Text(
                                _customSelected && _until != null
                                    ? 'on ${_dateLabel(_until!)}'
                                    : 'now',
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/widgets/history_screen_test.dart`
Expected: PASS (all 7 tests — 6 pre-existing plus the new Custom-chip test)

- [ ] **Step 5: Commit**

```bash
git add app/lib/screens/history/history_screen.dart app/test/widgets/history_screen_test.dart
git commit -m "feat: add Custom date-range chip to the history screen"
```

---

### Task 9: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Full Pi test suite**

Run: `cd pi && py -m pytest tests/ -q`
Expected: all tests pass — this now includes `test_history_query.py` (new), plus every pre-existing test in `test_recorder.py`, `test_portal_history.py`, and `test_weather_rules.py`, unmodified in their original assertions.

- [ ] **Step 2: Flutter static analysis**

Run: `cd app && flutter analyze`
Expected: `No issues found!` (fix anything it flags before continuing)

- [ ] **Step 3: Full Flutter test suite**

Run: `cd app && flutter test`
Expected: all tests pass, including every test added in Tasks 4–8 plus the entire pre-existing suite (zone_card, weather_card, history_prediction, connection, models, etc.) — this is the concrete check that nothing elsewhere in the app regressed.

- [ ] **Step 4: Release build smoke test**

Run: `cd app && flutter build apk --release`
Expected: `✓ Built build\app\outputs\flutter-apk\app-release.apk`

- [ ] **Step 5: Commit (only if Steps 1–4 required fixes)**

If any fixes were needed to make the Pi suite, analyzer, Flutter tests, or build pass cleanly:

```bash
git add -A
git commit -m "fix: address issues found in full verification pass for custom date-range picker"
```

If no fixes were needed, skip this step — there is nothing to commit.
