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

    Raises ValueError if only one of since/until is given -- both callers
    (the HTTP route and the MQTT handler) inherit this check instead of
    each deciding independently what a lone bound means.
    """
    if (since is None) != (until is None):
        raise ValueError('since and until must be provided together')
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
