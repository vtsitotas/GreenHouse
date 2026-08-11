# pi/tests/test_serial_bridge.py
import json
import os
import sys
from unittest.mock import MagicMock, call as mock_call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import serial_bridge


def _client():
    return MagicMock()


def _state():
    return serial_bridge.new_state()


def _line(d: dict) -> bytes:
    return (json.dumps(d) + '\n').encode()


# ── reading ──────────────────────────────────────────────────────────────────
def test_reading_line_publishes_to_correct_topic_with_formatted_payload():
    client = _client()
    line = _line({'type': 'reading', 'zone': 'zone1', 'group': 'air',
                   'metric': 'temperature', 'value': 23.44})

    serial_bridge.handle_line(client, line, _state())

    client.publish.assert_called_once_with(
        'greenhouse/zone1/air/temperature', '23.4', retain=True)


def test_reading_line_soil_moisture_topic_shape():
    client = _client()
    line = _line({'type': 'reading', 'zone': 'zone2', 'group': 'soil',
                   'metric': 'moisture', 'value': 42.0})

    serial_bridge.handle_line(client, line, _state())

    client.publish.assert_called_once_with(
        'greenhouse/zone2/soil/moisture', '42.0', retain=True)


# ── status ───────────────────────────────────────────────────────────────────
def test_status_line_publishes_to_node_status_topic():
    client = _client()
    line = _line({'type': 'status', 'mac': '206EF16CA1B0', 'status': 'online'})

    serial_bridge.handle_line(client, line, _state())

    client.publish.assert_called_once_with(
        'greenhouse/nodes/206EF16CA1B0/status', 'online', retain=True)


def test_status_line_offline():
    client = _client()
    line = _line({'type': 'status', 'mac': '206EF16CA1B0', 'status': 'offline'})

    serial_bridge.handle_line(client, line, _state())

    client.publish.assert_called_once_with(
        'greenhouse/nodes/206EF16CA1B0/status', 'offline', retain=True)


# ── battery ──────────────────────────────────────────────────────────────────
def test_battery_line_publishes_formatted_percent():
    client = _client()
    line = _line({'type': 'battery', 'mac': '206EF16CA1B0', 'pct': 87.456})

    serial_bridge.handle_line(client, line, _state())

    client.publish.assert_called_once_with(
        'greenhouse/nodes/206EF16CA1B0/battery', '87.5', retain=True)


# ── mesh ─────────────────────────────────────────────────────────────────────
def test_mesh_line_republishes_payload_with_all_fields_and_null_parent(monkeypatch):
    client = _client()
    monkeypatch.setattr(serial_bridge.time, 'time', lambda: 1754000000.7)
    line = _line({'type': 'mesh', 'mac': 'A0B1C2D3E4F5', 'parent': None,
                   'rank': 0, 'rssi': None, 'sleepy': False,
                   'battery_mv': None, 'zone': None})

    serial_bridge.handle_line(client, line, _state())

    client.publish.assert_called_once()
    args, kwargs = client.publish.call_args
    assert args[0] == 'greenhouse/nodes/A0B1C2D3E4F5/mesh'
    payload = json.loads(args[1])
    assert payload == {
        'parent': None, 'rank': 0, 'rssi': None,
        'sleepy': False, 'battery_mv': None, 'zone': None,
        'ts': 1754000000,  # truncated to whole seconds
    }
    assert kwargs.get('retain') is True


def test_mesh_line_republishes_payload_with_populated_fields(monkeypatch):
    client = _client()
    monkeypatch.setattr(serial_bridge.time, 'time', lambda: 1754000123.0)
    line = _line({'type': 'mesh', 'mac': '206EF16CA1B0', 'parent': 'A0B1C2D3E4F5',
                   'rank': 2, 'rssi': -61, 'sleepy': True,
                   'battery_mv': 3312, 'zone': 'zone1'})

    serial_bridge.handle_line(client, line, _state())

    args, kwargs = client.publish.call_args
    assert args[0] == 'greenhouse/nodes/206EF16CA1B0/mesh'
    payload = json.loads(args[1])
    assert payload == {
        'parent': 'A0B1C2D3E4F5', 'rank': 2, 'rssi': -61,
        'sleepy': True, 'battery_mv': 3312, 'zone': 'zone1',
        'ts': 1754000123,
    }
    assert kwargs.get('retain') is True


# ── mesh timestamping (the app's "last seen" depends entirely on this) ───────
# Every mesh topic is retained, and MQTT replays retained topics in full on
# every client (re)connect with no indication of age. Without `ts` in the
# payload the app can only date a node by when the replay arrived, which made
# every node read as "seen just now" on every app launch.
def test_mesh_ts_reflects_when_the_sighting_happened_not_when_it_is_read(monkeypatch):
    client, state = _client(), _state()
    monkeypatch.setattr(serial_bridge.time, 'time', lambda: 1754000500.0)
    serial_bridge.handle_line(client, _line(
        {'type': 'mesh', 'mac': '206EF16CA1B0', 'parent': 'A0B1C2D3E4F5', 'rank': 1,
         'rssi': -60, 'sleepy': True, 'battery_mv': 3300, 'zone': 'zone1'}), state)

    payload = json.loads(client.publish.call_args.args[1])
    assert payload['ts'] == 1754000500


def test_bridge_own_mesh_record_is_cached_and_refreshed_by_heartbeats(monkeypatch):
    """The firmware sends the bridge's own root record once, from setup().
    Its heartbeats are what actually prove it alive afterwards, so without
    this refresh the bridge's "last seen" would freeze at boot forever."""
    client, state = _client(), _state()
    wall, mono = [1754000000.0], [500.0]
    monkeypatch.setattr(serial_bridge.time, 'time', lambda: wall[0])
    monkeypatch.setattr(serial_bridge.time, 'monotonic', lambda: mono[0])

    # Boot order matters: the firmware sends this root record BEFORE the
    # first heartbeat, so the MAC isn't known yet -- it must be recognised
    # by parent=null + rank=0, not by matching state['bridge_mac'].
    serial_bridge.handle_line(client, _line(
        {'type': 'mesh', 'mac': 'A0B1C2D3E4F5', 'parent': None, 'rank': 0,
         'rssi': None, 'sleepy': False, 'battery_mv': None, 'zone': None}), state)
    assert state['bridge_mesh'] is not None
    client.publish.reset_mock()

    # First heartbeat: publishes online, and refreshes the mesh ts.
    wall[0] = 1754000060.0
    serial_bridge.handle_line(client, _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'}), state)
    mesh_pubs = [c for c in client.publish.call_args_list if c.args[0].endswith('/mesh')]
    assert len(mesh_pubs) == 1
    assert json.loads(mesh_pubs[0].args[1])['ts'] == 1754000060


def test_bridge_mesh_refresh_is_throttled_between_heartbeats(monkeypatch):
    """Heartbeats arrive every ~2s; this is a retained publish, so refreshing
    on every one of them would churn the broker for no added resolution."""
    client, state = _client(), _state()
    wall, mono = [1754000000.0], [500.0]
    monkeypatch.setattr(serial_bridge.time, 'time', lambda: wall[0])
    monkeypatch.setattr(serial_bridge.time, 'monotonic', lambda: mono[0])

    serial_bridge.handle_line(client, _line(
        {'type': 'mesh', 'mac': 'A0B1C2D3E4F5', 'parent': None, 'rank': 0,
         'rssi': None, 'sleepy': False, 'battery_mv': None, 'zone': None}), state)
    hb = _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'})
    serial_bridge.handle_line(client, hb, state)  # first refresh
    client.publish.reset_mock()

    mono[0] += serial_bridge.HEARTBEAT_INTERVAL_S  # next heartbeat, well inside the window
    serial_bridge.handle_line(client, hb, state)
    assert [c for c in client.publish.call_args_list if c.args[0].endswith('/mesh')] == []

    mono[0] += serial_bridge.BRIDGE_MESH_REFRESH_S  # window elapsed
    wall[0] = 1754000900.0
    serial_bridge.handle_line(client, hb, state)
    mesh_pubs = [c for c in client.publish.call_args_list if c.args[0].endswith('/mesh')]
    assert len(mesh_pubs) == 1
    assert json.loads(mesh_pubs[0].args[1])['ts'] == 1754000900


def test_heartbeat_refreshes_bridge_mesh_even_if_no_mesh_line_was_ever_seen(monkeypatch):
    """The firmware only sends its root record from setup(). Restarting or
    redeploying serial_bridge while the ESP32 keeps running -- the normal
    case for any service restart -- means no mesh line ever arrives, so
    without a synthesised fallback the bridge's "last seen" would stay
    unknown forever despite it heartbeating every 2s."""
    client, state = _client(), _state()
    monkeypatch.setattr(serial_bridge.time, 'time', lambda: 1754000777.0)
    monkeypatch.setattr(serial_bridge.time, 'monotonic', lambda: 500.0)
    assert state['bridge_mesh'] is None  # never saw one

    serial_bridge.handle_line(client, _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'}), state)

    mesh_pubs = [c for c in client.publish.call_args_list if c.args[0].endswith('/mesh')]
    assert len(mesh_pubs) == 1
    assert mesh_pubs[0].args[0] == 'greenhouse/nodes/A0B1C2D3E4F5/mesh'
    assert json.loads(mesh_pubs[0].args[1]) == {
        'parent': None, 'rank': 0, 'rssi': None,
        'sleepy': False, 'battery_mv': None, 'zone': None,
        'ts': 1754000777,
    }


def test_a_real_cached_bridge_record_wins_over_the_synthesised_fallback(monkeypatch):
    """Synthesising is only a fallback -- if the firmware did send its own
    record, that one is the source of truth (it may carry fields a future
    firmware adds that the hardcoded fallback wouldn't know about)."""
    client, state = _client(), _state()
    monkeypatch.setattr(serial_bridge.time, 'time', lambda: 1754000000.0)
    monkeypatch.setattr(serial_bridge.time, 'monotonic', lambda: 500.0)
    serial_bridge.handle_line(client, _line(
        {'type': 'mesh', 'mac': 'A0B1C2D3E4F5', 'parent': None, 'rank': 0,
         'rssi': -42, 'sleepy': False, 'battery_mv': 4100, 'zone': 'hub'}), state)
    client.publish.reset_mock()

    serial_bridge.handle_line(client, _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'}), state)

    mesh_pubs = [c for c in client.publish.call_args_list if c.args[0].endswith('/mesh')]
    payload = json.loads(mesh_pubs[0].args[1])
    assert payload['rssi'] == -42
    assert payload['battery_mv'] == 4100
    assert payload['zone'] == 'hub'


def test_an_edge_node_mesh_record_is_never_mistaken_for_the_bridges_own(monkeypatch):
    """Only parent=null AND rank=0 is the bridge. A rank-0-looking record
    that still has a parent, or a normal rank-1 leaf, must not be cached."""
    client, state = _client(), _state()
    monkeypatch.setattr(serial_bridge.time, 'time', lambda: 1754000000.0)

    serial_bridge.handle_line(client, _line(
        {'type': 'mesh', 'mac': '206EF16CA1B0', 'parent': 'A0B1C2D3E4F5', 'rank': 1,
         'rssi': -60, 'sleepy': True, 'battery_mv': 3300, 'zone': 'zone1'}), state)
    assert state['bridge_mesh'] is None

    serial_bridge.handle_line(client, _line(
        {'type': 'mesh', 'mac': '206EF16C9DB0', 'parent': 'A0B1C2D3E4F5', 'rank': 0,
         'rssi': -58, 'sleepy': True, 'battery_mv': 3200, 'zone': 'zone2'}), state)
    assert state['bridge_mesh'] is None


# ── malformed / unknown ──────────────────────────────────────────────────────
def test_malformed_json_line_does_not_crash_and_does_not_publish():
    client = _client()
    line = b'{"type":"reading", not valid json\n'

    serial_bridge.handle_line(client, line, _state())  # must not raise

    client.publish.assert_not_called()


def test_unknown_type_does_not_crash_and_does_not_publish():
    client = _client()
    line = _line({'type': 'unknown_thing', 'foo': 'bar'})

    serial_bridge.handle_line(client, line, _state())

    client.publish.assert_not_called()


def test_missing_type_field_does_not_crash_and_does_not_publish():
    client = _client()
    line = _line({'zone': 'zone1', 'value': 1.0})

    serial_bridge.handle_line(client, line, _state())

    client.publish.assert_not_called()


def test_reading_line_missing_expected_key_does_not_crash_and_does_not_publish():
    client = _client()
    line = _line({'type': 'reading', 'zone': 'zone1', 'group': 'air'})  # no metric/value

    serial_bridge.handle_line(client, line, _state())

    client.publish.assert_not_called()


# ── read timeout (empty bytes from mocked serial port) ─────────────────────
def test_empty_line_timeout_is_a_noop():
    client = _client()

    serial_bridge.handle_line(client, b'', _state())  # must not raise

    client.publish.assert_not_called()


# ── heartbeat-based bridge liveness ─────────────────────────────────────────
def _status_publishes(client):
    """Only the /status publishes. A heartbeat also refreshes the bridge's
    own /mesh record (see BRIDGE_MESH_REFRESH_S), so counting every publish
    would conflate the two independent behaviours."""
    return [c for c in client.publish.call_args_list if c.args[0].endswith('/status')]


def test_first_heartbeat_ever_publishes_online():
    client = _client()
    state = _state()
    line = _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'})

    serial_bridge.handle_line(client, line, state)

    assert _status_publishes(client) == [
        mock_call('greenhouse/nodes/A0B1C2D3E4F5/status', 'online', retain=True)]


def test_second_heartbeat_shortly_after_does_not_republish_online(monkeypatch):
    client = _client()
    state = _state()
    fake_now = [1000.0]
    monkeypatch.setattr(serial_bridge.time, 'monotonic', lambda: fake_now[0])

    line = _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'})
    serial_bridge.handle_line(client, line, state)
    assert len(_status_publishes(client)) == 1

    fake_now[0] += 2.0  # normal heartbeat cadence, well under offline threshold
    serial_bridge.handle_line(client, line, state)

    assert len(_status_publishes(client)) == 1  # no duplicate "online" publish


def test_offline_check_publishes_offline_after_missed_heartbeats(monkeypatch):
    client = _client()
    state = _state()
    fake_now = [1000.0]
    monkeypatch.setattr(serial_bridge.time, 'monotonic', lambda: fake_now[0])

    line = _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'})
    serial_bridge.handle_line(client, line, state)
    client.publish.reset_mock()

    # advance past HEARTBEAT_INTERVAL_S * HEARTBEAT_OFFLINE_AFTER with no heartbeat
    fake_now[0] += (serial_bridge.HEARTBEAT_INTERVAL_S
                     * serial_bridge.HEARTBEAT_OFFLINE_AFTER) + 0.1

    serial_bridge.check_heartbeat_offline(client, state)

    client.publish.assert_called_once_with(
        'greenhouse/nodes/A0B1C2D3E4F5/status', 'offline', retain=True)


def test_offline_check_does_not_republish_offline_repeatedly(monkeypatch):
    client = _client()
    state = _state()
    fake_now = [1000.0]
    monkeypatch.setattr(serial_bridge.time, 'monotonic', lambda: fake_now[0])

    line = _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'})
    serial_bridge.handle_line(client, line, state)

    fake_now[0] += (serial_bridge.HEARTBEAT_INTERVAL_S
                     * serial_bridge.HEARTBEAT_OFFLINE_AFTER) + 0.1
    serial_bridge.check_heartbeat_offline(client, state)
    client.publish.reset_mock()

    fake_now[0] += 5.0
    serial_bridge.check_heartbeat_offline(client, state)

    client.publish.assert_not_called()


def test_offline_check_noop_before_any_heartbeat_seen():
    client = _client()
    state = _state()

    serial_bridge.check_heartbeat_offline(client, state)  # must not raise

    client.publish.assert_not_called()


def test_heartbeat_after_offline_period_republishes_online(monkeypatch):
    client = _client()
    state = _state()
    fake_now = [1000.0]
    monkeypatch.setattr(serial_bridge.time, 'monotonic', lambda: fake_now[0])

    line = _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'})
    serial_bridge.handle_line(client, line, state)  # first ever -> online

    fake_now[0] += (serial_bridge.HEARTBEAT_INTERVAL_S
                     * serial_bridge.HEARTBEAT_OFFLINE_AFTER) + 0.1
    serial_bridge.check_heartbeat_offline(client, state)  # -> offline
    client.publish.reset_mock()

    fake_now[0] += 1.0
    serial_bridge.handle_line(client, line, state)  # heartbeat resumes -> online

    client.publish.assert_called_once_with(
        'greenhouse/nodes/A0B1C2D3E4F5/status', 'online', retain=True)


# ── startup staleness sweep ──────────────────────────────────────────────────
# Regression: liveness tracking only begins once a heartbeat has been seen, so
# restarting the service while the ESP32 was dead left every retained "online"
# standing indefinitely -- MQTT showed a healthy fleet while the UART had been
# silent for hours (observed on the bench 2026-08-10).
def test_retained_online_is_recorded_at_startup():
    state = _state()
    serial_bridge.note_retained_status(state, 'greenhouse/nodes/AABBCC/status', b'online')
    assert state['retained_online'] == {'AABBCC'}


def test_retained_offline_is_not_recorded():
    state = _state()
    serial_bridge.note_retained_status(state, 'greenhouse/nodes/AABBCC/status', b'offline')
    assert state['retained_online'] == set()


def test_unrelated_topic_is_ignored():
    state = _state()
    serial_bridge.note_retained_status(state, 'greenhouse/zone1/air/temperature', b'online')
    assert state['retained_online'] == set()


def test_sweep_marks_stale_nodes_offline_when_no_heartbeat_arrives(monkeypatch):
    client, state = _client(), _state()
    serial_bridge.note_retained_status(state, 'greenhouse/nodes/AABBCC/status', b'online')
    serial_bridge.note_retained_status(state, 'greenhouse/nodes/DDEEFF/status', b'online')
    monkeypatch.setattr(serial_bridge.time, 'monotonic',
                        lambda: state['started'] + serial_bridge.HEARTBEAT_TIMEOUT_S + 1)
    serial_bridge.sweep_stale_online(client, state)
    published = {c.args[0]: c.args[1] for c in client.publish.call_args_list}
    assert published == {'greenhouse/nodes/AABBCC/status': 'offline',
                         'greenhouse/nodes/DDEEFF/status': 'offline'}
    assert state['startup_swept'] is True


def test_sweep_does_not_fire_before_the_timeout_window(monkeypatch):
    client, state = _client(), _state()
    serial_bridge.note_retained_status(state, 'greenhouse/nodes/AABBCC/status', b'online')
    monkeypatch.setattr(serial_bridge.time, 'monotonic',
                        lambda: state['started'] + serial_bridge.HEARTBEAT_TIMEOUT_S - 0.5)
    serial_bridge.sweep_stale_online(client, state)
    client.publish.assert_not_called()
    assert state['startup_swept'] is False


def test_sweep_does_not_fire_when_a_heartbeat_was_seen(monkeypatch):
    """A live bridge must never have its own nodes retracted."""
    client, state = _client(), _state()
    serial_bridge.note_retained_status(state, 'greenhouse/nodes/AABBCC/status', b'online')
    serial_bridge.handle_line(client, _line({'type': 'heartbeat', 'mac': 'AABBCC'}), state)
    client.reset_mock()
    monkeypatch.setattr(serial_bridge.time, 'monotonic',
                        lambda: state['started'] + serial_bridge.HEARTBEAT_TIMEOUT_S + 1)
    serial_bridge.sweep_stale_online(client, state)
    client.publish.assert_not_called()


def test_sweep_runs_at_most_once(monkeypatch):
    client, state = _client(), _state()
    serial_bridge.note_retained_status(state, 'greenhouse/nodes/AABBCC/status', b'online')
    monkeypatch.setattr(serial_bridge.time, 'monotonic',
                        lambda: state['started'] + serial_bridge.HEARTBEAT_TIMEOUT_S + 1)
    serial_bridge.sweep_stale_online(client, state)
    client.reset_mock()
    serial_bridge.sweep_stale_online(client, state)
    client.publish.assert_not_called()


def test_retained_status_ignored_after_sweep_completed():
    state = _state()
    state['startup_swept'] = True
    serial_bridge.note_retained_status(state, 'greenhouse/nodes/AABBCC/status', b'online')
    assert state['retained_online'] == set()
