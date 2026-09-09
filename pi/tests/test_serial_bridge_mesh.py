# pi/tests/test_serial_bridge_mesh.py
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
    assert c.published == []


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
