# pi/tests/test_firmware_layout.py
"""Cross-checks the firmware's byte layout against the Pi's.

The harness binary is plain C++ with no Arduino or mbedTLS dependency, so it
builds anywhere g++ does. If g++ is unavailable the test skips — CI always has
it, so the check is never silently lost.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))

import mesh_packet as mp

HERE = os.path.dirname(os.path.abspath(__file__))
HOST_DIR = os.path.normpath(os.path.join(HERE, '..', '..', 'firmware', 'test', 'host'))
MAC = bytes.fromhex('206EF16C9DB0')
PARENT = bytes.fromhex('206EF16CBE80')


@pytest.fixture(scope='module')
def vectors():
    try:
        rc = subprocess.call(['g++', '--version'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        rc = 1
    if rc != 0:
        pytest.skip('g++ not available')
    subprocess.check_call(['make', '-s'], cwd=HOST_DIR)
    out = subprocess.check_output([os.path.join(HOST_DIR, 'layout_vectors')], text=True)
    return dict(line.split('=', 1) for line in out.strip().splitlines())


def test_firmware_header_matches_python_byte_for_byte(vectors):
    expected = mp.pack_header(MAC, seq=0x1234, boot_count=0x0A0B0C0D,
                              flags=0x01, rank=2, ttl=4)
    assert vectors['header'] == expected.hex()


def test_firmware_body_matches_python_byte_for_byte(vectors):
    expected = mp.pack_body(21.5, 60.25, 42.0, 3700, PARENT, -67)
    assert vectors['body'] == expected.hex()


def test_firmware_nonce_matches_python_byte_for_byte(vectors):
    expected = mp.build_nonce(MAC, boot_count=0x0A0B0C0D, seq=0x1234)
    assert vectors['nonce'] == expected.hex()


def test_firmware_and_python_agree_on_every_length(vectors):
    assert int(vectors['header_len']) == mp.HEADER_LEN
    assert int(vectors['body_len']) == mp.BODY_LEN
    assert int(vectors['packet_len']) == mp.PACKET_LEN
    assert int(vectors['aad_len']) == mp.AAD_LEN
    assert int(vectors['magic']) == mp.MESH_MAGIC_V2