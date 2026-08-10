# pi/tests/test_serial_bridge.py
import json
import os
import sys
from unittest.mock import MagicMock

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
def test_mesh_line_republishes_payload_with_all_fields_and_null_parent():
    client = _client()
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
    }
    assert kwargs.get('retain') is True


def test_mesh_line_republishes_payload_with_populated_fields():
    client = _client()
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
    }
    assert kwargs.get('retain') is True


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
def test_first_heartbeat_ever_publishes_online():
    client = _client()
    state = _state()
    line = _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'})

    serial_bridge.handle_line(client, line, state)

    client.publish.assert_called_once_with(
        'greenhouse/nodes/A0B1C2D3E4F5/status', 'online', retain=True)


def test_second_heartbeat_shortly_after_does_not_republish_online(monkeypatch):
    client = _client()
    state = _state()
    fake_now = [1000.0]
    monkeypatch.setattr(serial_bridge.time, 'monotonic', lambda: fake_now[0])

    line = _line({'type': 'heartbeat', 'mac': 'A0B1C2D3E4F5'})
    serial_bridge.handle_line(client, line, state)
    assert client.publish.call_count == 1

    fake_now[0] += 2.0  # normal heartbeat cadence, well under offline threshold
    serial_bridge.handle_line(client, line, state)

    assert client.publish.call_count == 1  # no duplicate "online" publish


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
