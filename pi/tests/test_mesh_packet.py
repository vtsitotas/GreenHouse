# pi/tests/test_mesh_packet.py
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))

import mesh_packet as mp

MAC = bytes.fromhex('206EF16C9DB0')
PARENT = bytes.fromhex('206EF16CBE80')


def test_header_is_16_bytes_with_fields_at_spec_offsets():
    raw = mp.pack_header(MAC, seq=0x1234, boot_count=0x0A0B0C0D,
                         flags=0x01, rank=2, ttl=4)
    assert len(raw) == mp.HEADER_LEN == 16
    assert raw[0] == mp.MESH_MAGIC_V2 == 0x48
    assert raw[1:7] == MAC
    assert raw[7:9] == b'\x34\x12'                  # seq, little-endian
    assert raw[9:13] == b'\x0d\x0c\x0b\x0a'         # boot_count, little-endian
    assert raw[13] == 0x01
    assert raw[14] == 2
    assert raw[15] == 4


def test_parse_header_round_trips():
    raw = mp.pack_header(MAC, seq=7, boot_count=9, flags=0, rank=1, ttl=3)
    h = mp.parse_header(raw)
    assert h == mp.MeshHeader(mp.MESH_MAGIC_V2, MAC, 7, 9, 0, 1, 3)


def test_aad_excludes_the_mutable_ttl_byte():
    a = mp.pack_header(MAC, seq=7, boot_count=9, flags=0, rank=1, ttl=4)
    b = mp.pack_header(MAC, seq=7, boot_count=9, flags=0, rank=1, ttl=1)
    assert a != b                                    # ttl differs
    assert mp.header_aad(a) == mp.header_aad(b)      # but the AAD does not
    assert len(mp.header_aad(a)) == 15


def test_nonce_is_mac_bootcount_seq_and_exactly_12_bytes():
    n = mp.build_nonce(MAC, boot_count=0x0A0B0C0D, seq=0x1234)
    assert n == MAC + b'\x0d\x0c\x0b\x0a' + b'\x34\x12'
    assert len(n) == 12


def test_body_is_21_bytes_and_round_trips():
    raw = mp.pack_body(21.5, 60.25, 42.0, 3700, PARENT, -67)
    assert len(raw) == mp.BODY_LEN == 21
    b = mp.parse_body(raw)
    assert b.temperature == pytest.approx(21.5)
    assert b.humidity == pytest.approx(60.25)
    assert b.soil_moisture == pytest.approx(42.0)
    assert b.battery_mv == 3700
    assert b.parent_mac == PARENT
    assert b.parent_rssi == -67


def test_parse_header_rejects_the_old_v1_magic():
    raw = bytearray(mp.pack_header(MAC, 1, 1, 0, 1, 1))
    raw[0] = 0x47                                    # the v1 magic
    with pytest.raises(ValueError):
        mp.parse_header(bytes(raw))
