# pi/tests/test_uart_framing.py — binary bridge<->Pi framing (mirror of mesh_uart.h)
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
import uart_framing as uf

# Same vectors as firmware/test/host/framing_tests.cpp: both ends agree bit for bit.
ACK_VEC = bytes.fromhex('a55a810aaabbccddeeff341201079841')
JSON_VEC = bytes.fromhex('a55a02107b2274797065223a2268656c6c6f227d52e2')


def test_crc_is_ccitt_false():
    assert uf.crc16(b'123456789') == 0x29B1


def test_encoders_match_the_firmware_vectors():
    assert uf.encode_ack('AABBCCDDEEFF', 0x1234, True, 7) == ACK_VEC
    assert uf.encode(uf.T_JSON, b'{"type":"hello"}') == JSON_VEC
    frame = uf.encode(uf.T_FRAME, bytes(range(61)))
    assert len(frame) == 67 and frame[-2:] == bytes.fromhex('ff48')


def test_oversized_payload_is_refused():
    with pytest.raises(ValueError):
        uf.encode(uf.T_JSON, bytes(251))


def test_decoder_handles_binary_and_lines_in_one_stream():
    d = uf.Decoder()
    stream = b'{"type":"heartbeat","mac":"AA"}\r\n' + ACK_VEC + JSON_VEC
    events = d.feed(stream)
    assert events[0] == ('line', b'{"type":"heartbeat","mac":"AA"}')
    assert events[1] == ('frame', uf.T_ACK, ACK_VEC[4:14])
    assert events[2] == ('frame', uf.T_JSON, b'{"type":"hello"}')


def test_decoder_reassembles_byte_by_byte():
    d = uf.Decoder()
    out = []
    for b in ACK_VEC + b'{"a":1}\n':
        out += d.feed(bytes([b]))
    assert [e[0] for e in out] == ['frame', 'line']


def test_bad_crc_is_dropped_and_the_next_frame_survives():
    d = uf.Decoder()
    bad = bytearray(ACK_VEC)
    bad[8] ^= 1
    assert d.feed(bytes(bad)) == []
    assert d.bad_crc == 1
    assert d.feed(ACK_VEC)[0][1] == uf.T_ACK


def test_noise_and_a_torn_line_do_not_eat_the_next_frame():
    d = uf.Decoder()
    events = d.feed(b'\x00\x13\xa5\x00{xy' + ACK_VEC)
    assert [e[:2] for e in events] == [('frame', uf.T_ACK)]


# ── serial_bridge integration ────────────────────────────────────────────────
APP = bytes(range(16))
NET = bytes(range(16, 32))
MAC = bytes.fromhex('206EF16C9DB0')
MAC_S = '206EF16C9DB0'


class FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))


class FakeSerial:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)


@pytest.fixture
def state(tmp_path):
    store = str(tmp_path / 'nodes.json')
    nodes.add(nodes.Node(MAC_S, APP, 'zone2', 'Tomato bed', True), store)
    st = sb.new_state()
    st['nodes_path'] = store
    st['net_key'] = NET
    return st


def _sealed(seq=1):
    body = mp.pack_body(21.5, 60.0, 42.0, 3700, bytes(6), -67)
    return mc.seal_packet(APP, NET, MAC, seq=seq, boot_count=1, flags=0, rank=1, ttl=4, body=body)


def test_a_binary_frame_is_published_and_acked_in_binary(state):
    c, link = FakeClient(), sb.BridgeLink(FakeSerial())
    n = sb.dispatch_bytes(c, link, uf.encode(uf.T_FRAME, _sealed()), state)
    assert n == 1 and link.binary
    assert any('zone2' in topic for topic, _p, _r in c.published)
    ack = link.ser.written[-1]
    assert ack[:3] == b'\xa5\x5a\x81'                         # binary ack, not a JSON line
    assert uf.Decoder().feed(ack)[0][2] == bytes.fromhex(MAC_S) + b'\x01\x00\x01\x03'


def test_a_hex_json_bridge_still_gets_json_acks(state):
    c, link = FakeClient(), sb.BridgeLink(FakeSerial())
    line = (json.dumps({'type': 'frame', 'data': _sealed().hex()}) + '\n').encode()
    assert sb.dispatch_bytes(c, link, line, state) == 1
    assert not link.binary
    assert link.ser.written[-1].endswith(b'\n') and b'"ack"' in link.ser.written[-1]


def test_binary_json_messages_reach_the_json_handlers(state):
    c, link = FakeClient(), sb.BridgeLink(FakeSerial())
    msg = json.dumps({'type': 'heartbeat', 'mac': 'AABBCCDDEEFF'}).encode()
    assert sb.dispatch_bytes(c, link, uf.encode(uf.T_JSON, msg), state) == 1
    assert state['bridge_mac'] == 'AABBCCDDEEFF' and state['bridge_online']


def test_garbage_at_the_wrong_baud_counts_as_nothing_valid(state):
    link = sb.BridgeLink(FakeSerial())
    assert sb.dispatch_bytes(FakeClient(), link, bytes([0xF0, 0x0F, 0xFF, 0x80] * 20), state) == 0
