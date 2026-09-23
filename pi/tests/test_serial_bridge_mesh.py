# pi/tests/test_serial_bridge_mesh.py
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import mesh_crypto as mc
import mesh_packet as mp
import nodes
import serial_bridge as sb

APP = bytes(range(16))
NET = bytes(range(16, 32))
MAC = bytes.fromhex('206EF16C9DB0')
MAC_S = '206EF16C9DB0'
PARENT = bytes.fromhex('206EF16CBE80')


class FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))


@pytest.fixture
def state(tmp_path):
    store = str(tmp_path / 'nodes.json')
    nodes.add(nodes.Node(MAC_S, APP, 'zone2', 'Tomato bed', True), store)
    st = sb.new_state()
    st['nodes_path'] = store
    st['net_key'] = NET
    return st


def _frame(seq=1, boot=1):
    body = mp.pack_body(21.5, 60.0, 42.0, 3700, PARENT, -67)
    raw = mc.seal_packet(APP, NET, MAC, seq=seq, boot_count=boot,
                         flags=0, rank=1, ttl=4, body=body)
    return {'type': 'frame', 'data': raw.hex()}


def test_a_valid_frame_publishes_readings_on_the_zone_topic(state):
    c = FakeClient()
    sb.handle_frame(c, _frame(), state)
    topics = [t for t, _, _ in c.published]
    assert any('zone2' in t and t.endswith('temperature') for t in topics)


def test_a_frame_from_an_unknown_mac_is_dropped_not_crashed(state):
    other = mc.seal_packet(bytes(16), NET, bytes.fromhex('AABBCCDDEEFF'),
                           seq=1, boot_count=1, flags=0, rank=1, ttl=4,
                           body=mp.pack_body(1, 1, 1, 0, PARENT, 0))
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': other.hex()}, state)
    assert c.published == []


def test_a_frame_that_fails_authentication_is_dropped(state):
    f = _frame()
    raw = bytearray(bytes.fromhex(f['data']))
    raw[30] ^= 0x01
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': bytes(raw).hex()}, state)
    # No reading/battery/status topic -- the body never decrypted.
    assert not any(t.endswith(('temperature', 'humidity', 'moisture', 'status', 'battery'))
                   for t, _, _ in c.published)


def test_a_frame_that_fails_authentication_flags_the_mesh_map(state):
    f = _frame()
    raw = bytearray(bytes.fromhex(f['data']))
    raw[30] ^= 0x01
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': bytes(raw).hex()}, state)
    mesh_pubs = [(t, p) for t, p, _ in c.published if t.endswith('/mesh')]
    assert len(mesh_pubs) == 1
    payload = json.loads(mesh_pubs[0][1])
    assert payload['last_ack'] == 'rejected'
    assert payload['parent'] is None
    assert payload['zone'] == 'zone2'   # from the trust store, not the (undecrypted) body


def test_a_valid_frame_marks_the_mesh_map_accepted(state):
    c = FakeClient()
    sb.handle_frame(c, _frame(), state)
    mesh_pubs = [(t, p) for t, p, _ in c.published if t.endswith('/mesh')]
    assert len(mesh_pubs) == 1
    assert json.loads(mesh_pubs[0][1])['last_ack'] == 'accepted'


def test_unenrolled_frame_logs_a_security_event(state, monkeypatch):
    logged = []
    monkeypatch.setattr(sb, 'log_security_event', lambda *a, **kw: logged.append((a, kw)))
    other = mc.seal_packet(bytes(16), NET, bytes.fromhex('AABBCCDDEEFF'),
                           seq=1, boot_count=1, flags=0, rank=1, ttl=4,
                           body=mp.pack_body(1, 1, 1, 0, PARENT, 0))
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': other.hex()}, state)
    assert logged == [(('mesh_unenrolled_frame',), {'source': 'AABBCCDDEEFF'})]


def test_replay_drop_logs_a_security_event(state, monkeypatch):
    logged = []
    monkeypatch.setattr(sb, 'log_security_event', lambda *a, **kw: logged.append((a, kw)))
    c = FakeClient()
    sb.handle_frame(c, _frame(), state)   # first delivery: accepted, not logged
    logged.clear()
    sb.handle_frame(c, _frame(), state)   # same (boot_count, seq): replay
    assert logged == [(('mesh_replay_dropped',), {'source': MAC_S})]


def test_auth_failure_logs_a_security_event(state, monkeypatch):
    logged = []
    monkeypatch.setattr(sb, 'log_security_event', lambda *a, **kw: logged.append((a, kw)))
    f = _frame()
    raw = bytearray(bytes.fromhex(f['data']))
    raw[30] ^= 0x01
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': bytes(raw).hex()}, state)
    assert len(logged) == 1
    args, kwargs = logged[0]
    assert args == ('mesh_auth_failure',)
    assert kwargs['source'] == MAC_S
    assert 'detail' in kwargs and kwargs['detail']


def test_replayed_seq_under_the_same_boot_count_is_rejected(state):
    assert sb.accept_replay(state, MAC_S, boot_count=1, seq=5) is True
    assert sb.accept_replay(state, MAC_S, boot_count=1, seq=5) is False


def test_a_lower_boot_count_is_rejected_as_a_rollback(state):
    assert sb.accept_replay(state, MAC_S, boot_count=4, seq=1) is True
    assert sb.accept_replay(state, MAC_S, boot_count=3, seq=99) is False


def test_a_new_boot_count_resets_the_seq_window(state):
    assert sb.accept_replay(state, MAC_S, boot_count=1, seq=9) is True
    assert sb.accept_replay(state, MAC_S, boot_count=2, seq=1) is True


def test_unenrolled_macs_are_remembered_for_the_app(state):
    sb.handle_unenrolled({'type': 'unenrolled', 'mac': MAC_S}, state)
    assert MAC_S in sb.unenrolled_macs(state)


def test_send_netkey_writes_one_json_line():
    written = []
    sb.send_netkey(type('S', (), {'write': lambda _s, b: written.append(b)})(), NET)
    assert written[0].endswith(b'\n')
    assert b'"netkey"' in written[0] and NET.hex().encode() in written[0]


def test_send_ack_writes_one_json_line():
    written = []
    sb.send_ack(type('S', (), {'write': lambda _s, b: written.append(b)})(), MAC_S, 7, True)
    assert written[0].endswith(b'\n')
    assert b'"ack"' in written[0]
    assert b'"seq":7' in written[0]
    assert b'"ok":true' in written[0]


def test_a_valid_frame_sends_a_positive_ack(state, monkeypatch):
    acked = []
    monkeypatch.setattr(sb, 'send_ack', lambda *a, **kw: acked.append((a, kw)))
    sentinel_ser = object()
    c = FakeClient()
    sb.handle_frame(c, _frame(), state, sentinel_ser)
    assert acked == [((sentinel_ser, MAC_S, 1), {'ok': True, 'ttl': 3})]


def test_an_auth_failure_sends_a_negative_ack(state, monkeypatch):
    acked = []
    monkeypatch.setattr(sb, 'send_ack', lambda *a, **kw: acked.append((a, kw)))
    f = _frame()
    raw = bytearray(bytes.fromhex(f['data']))
    raw[30] ^= 0x01
    sentinel_ser = object()
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': bytes(raw).hex()}, state, sentinel_ser)
    assert acked == [((sentinel_ser, MAC_S, 1), {'ok': False, 'ttl': 3})]


def test_send_ack_carries_ttl_when_given():
    written = []
    sb.send_ack(type('S', (), {'write': lambda _s, b: written.append(b)})(), MAC_S, 7, True, ttl=4)
    assert b'"ttl":4' in written[0]


def test_handle_frame_without_a_serial_connection_does_not_crash(state):
    # Backward compatibility: every pre-existing call site/test omits ser.
    c = FakeClient()
    sb.handle_frame(c, _frame(), state)
    topics = [t for t, _, _ in c.published]
    assert any('temperature' in t for t in topics)


def test_unenrolled_and_replay_drops_do_not_send_an_ack(state, monkeypatch):
    acked = []
    monkeypatch.setattr(sb, 'send_ack', lambda *a, **kw: acked.append((a, kw)))
    sentinel_ser = object()
    c = FakeClient()
    other = mc.seal_packet(bytes(16), NET, bytes.fromhex('AABBCCDDEEFF'),
                           seq=1, boot_count=1, flags=0, rank=1, ttl=4,
                           body=mp.pack_body(1, 1, 1, 0, PARENT, 0))
    sb.handle_frame(c, {'type': 'frame', 'data': other.hex()}, state, sentinel_ser)
    sb.handle_frame(c, _frame(), state, sentinel_ser)   # first: accepted
    acked.clear()
    sb.handle_frame(c, _frame(), state, sentinel_ser)   # same seq again: replay
    assert acked == []


def test_load_net_key_reads_hex_from_disk(tmp_path):
    path = tmp_path / 'netkey'
    path.write_text(NET.hex() + '\n')
    assert sb.load_net_key(str(path)) == NET


def test_load_net_key_rejects_a_key_that_is_not_16_bytes(tmp_path):
    path = tmp_path / 'netkey'
    path.write_text('aabb')
    with pytest.raises(ValueError):
        sb.load_net_key(str(path))


def test_load_net_key_raises_when_the_file_is_missing(tmp_path):
    with pytest.raises(OSError):
        sb.load_net_key(str(tmp_path / 'does-not-exist'))


def test_unenrolled_line_records_capabilities(state):
    sb.handle_unenrolled({'type': 'unenrolled', 'mac': MAC_S, 'caps': 1}, state)
    assert state['caps'][MAC_S] == 1


def test_unenrolled_line_without_caps_means_old_firmware(state):
    sb.handle_unenrolled({'type': 'unenrolled', 'mac': MAC_S}, state)
    assert state['caps'][MAC_S] == 0
