# pi/tests/test_rak3172.py
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import rak3172 as rk


class FakeSerial:
    """Replies to each written command with a scripted list of lines."""
    def __init__(self, script):
        self.script = dict(script)
        self.pending = []
        self.written = []

    def write(self, data):
        cmd = data.decode().strip()
        self.written.append(cmd)
        self.pending.extend(self.script.get(cmd, ['OK']))

    def readline(self):
        return (self.pending.pop(0) + '\r\n').encode() if self.pending else b''


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.25
        return self.t


def test_parse_class_c_downlink():
    evt = rk.parse_event('+EVT:RX_C:-70:8:UNICAST:10:0101')
    assert evt == {'type': 'rx', 'window': 'C', 'rssi': -70, 'snr': 8, 'port': 10,
                   'payload': b'\x01\x01'}


def test_parse_simple_events():
    assert rk.parse_event('+EVT:JOINED') == {'type': 'joined'}
    assert rk.parse_event('+EVT:TX_DONE') == {'type': 'tx_done'}
    assert rk.parse_event('+EVT:JOIN_FAILED_RX_TIMEOUT') == {'type': 'join_failed'}
    assert rk.parse_event('OK') is None


def test_command_returns_lines_before_ok():
    ser = FakeSerial({'AT+NJS=?': ['AT+NJS=1', 'OK']})
    assert rk.Rak3172(ser, clock=FakeClock()).command('AT+NJS=?') == ['AT+NJS=1']


def test_command_error_raises():
    ser = FakeSerial({'AT+SEND=1:00': ['AT_NO_NETWORK_JOINED']})
    with pytest.raises(rk.RakError):
        rk.Rak3172(ser, clock=FakeClock()).command('AT+SEND=1:00')


def test_command_timeout_raises():
    ser = FakeSerial({'AT': []})
    with pytest.raises(rk.RakError):
        rk.Rak3172(ser, clock=FakeClock()).command('AT', timeout=1.0)


def test_configure_otaa_sends_eu868_class_c_sequence():
    ser = FakeSerial({})
    rk.Rak3172(ser, clock=FakeClock()).configure_otaa('0011223344556677', '0000000000000000',
                                                      '00' * 16)
    assert ser.written == ['AT+NWM=1', 'AT+BAND=4', 'AT+NJM=1', 'AT+CLASS=C', 'AT+ADR=1',
                           'AT+DEVEUI=0011223344556677', 'AT+APPEUI=0000000000000000',
                           'AT+APPKEY=' + '00' * 16]


def test_join_waits_for_the_joined_event():
    ser = FakeSerial({'AT+JOIN=1:0:10:8': ['OK', '+EVT:JOINED']})
    assert rk.Rak3172(ser, clock=FakeClock()).join(timeout=10) is True


def test_send_waits_for_tx_done_and_keeps_downlinks_queued():
    ser = FakeSerial({'AT+SEND=1:0102': ['OK', '+EVT:RX_C:-80:5:UNICAST:10:0100', '+EVT:TX_DONE']})
    dev = rk.Rak3172(ser, clock=FakeClock())
    assert dev.send(1, b'\x01\x02') is True
    assert dev.poll_events()[0]['port'] == 10


def test_send_returns_false_on_tx_failed():
    ser = FakeSerial({'AT+SEND=1:0102': ['OK', '+EVT:SEND_CONFIRMED_FAILED']})
    dev = rk.Rak3172(ser, clock=FakeClock())
    assert dev.send(1, b'\x01\x02') is False


def test_parse_malformed_rx_line_does_not_raise():
    # non-hex payload
    evt = rk.parse_event('+EVT:RX_C:-70:8:UNICAST:10:ZZZZ')
    assert evt == {'type': 'other', 'line': '+EVT:RX_C:-70:8:UNICAST:10:ZZZZ'}
    # non-numeric rssi
    evt = rk.parse_event('+EVT:RX_C:abc:8:UNICAST:10:0101')
    assert evt == {'type': 'other', 'line': '+EVT:RX_C:abc:8:UNICAST:10:0101'}
