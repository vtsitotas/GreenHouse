# pi/tests/test_firmware_vectors.py
"""Proves the device's real mbedTLS output is what the Pi's cryptography reads.

The vector file is produced by flashing firmware/crypto_selftest and pasting the
one line it prints over serial. Regenerate it whenever the packet format changes;
a stale file failing here is the point.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))

import mesh_crypto as mc
import mesh_packet as mp

VECTORS = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', 'firmware', 'test', 'vectors',
    'device_vectors.txt'))

APP = bytes(range(16))
NET = bytes(range(16, 32))
PARENT = bytes.fromhex('206EF16CBE80')


@pytest.fixture(scope='module')
def device_packet():
    if not os.path.exists(VECTORS):
        pytest.skip('no device vectors captured yet — see firmware/crypto_selftest')
    for line in open(VECTORS):
        if line.startswith('packet='):
            return bytes.fromhex(line.strip().split('=', 1)[1])
    pytest.skip('vector file has no packet= line')


def test_pi_opens_a_packet_the_device_actually_sealed(device_packet):
    body = mc.open_packet(device_packet, APP)
    assert body.battery_mv == 3700
    assert body.parent_rssi == -67
    assert body.parent_mac == PARENT


def test_pi_accepts_the_device_nettag(device_packet):
    assert mc.verify_nettag(device_packet, NET)


def test_pi_and_device_produce_byte_identical_packets(device_packet):
    body = mp.pack_body(21.5, 60.0, 42.0, 3700, PARENT, -67)
    ours = mc.seal_packet(APP, NET, bytes.fromhex('206EF16C9DB0'),
                          seq=7, boot_count=3, flags=0, rank=1, ttl=4, body=body)
    assert ours == device_packet
