# pi/tests/test_mesh_crypto.py
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))

import mesh_crypto as mc
import mesh_packet as mp

APP = bytes(range(16))
NET = bytes(range(16, 32))
MAC = bytes.fromhex('206EF16C9DB0')
PARENT = bytes.fromhex('206EF16CBE80')
BODY = mp.pack_body(21.5, 60.0, 42.0, 3700, PARENT, -67)


def _packet(ttl=4, seq=7, boot=3):
    return mc.seal_packet(APP, NET, MAC, seq=seq, boot_count=boot,
                          flags=0, rank=1, ttl=ttl, body=BODY)


def test_sealed_packet_is_exactly_61_bytes():
    assert len(_packet()) == mp.PACKET_LEN == 61


def test_open_recovers_the_body():
    b = mc.open_packet(_packet(), APP)
    assert b.battery_mv == 3700
    assert b.parent_rssi == -67
    assert b.parent_mac == PARENT


def test_open_rejects_a_wrong_app_key():
    with pytest.raises(mc.MeshAuthError):
        mc.open_packet(_packet(), bytes(16))


def test_open_rejects_a_flipped_ciphertext_bit():
    raw = bytearray(_packet())
    raw[30] ^= 0x01
    with pytest.raises(mc.MeshAuthError):
        mc.open_packet(bytes(raw), APP)


def test_open_rejects_a_tampered_header_because_it_is_authenticated():
    raw = bytearray(_packet())
    raw[14] = 9                                  # rank, inside the AAD
    with pytest.raises(mc.MeshAuthError):
        mc.open_packet(bytes(raw), APP)


def test_ttl_may_change_in_flight_without_breaking_either_tag():
    raw = bytearray(_packet(ttl=4))
    raw[15] = 1                                  # a relay decremented it
    assert mc.verify_nettag(bytes(raw), NET)
    assert mc.open_packet(bytes(raw), APP).battery_mv == 3700


def test_nettag_rejects_a_wrong_net_key():
    assert not mc.verify_nettag(_packet(), bytes(16))


def test_same_body_under_different_seq_gives_different_ciphertext():
    a, b = _packet(seq=1), _packet(seq=2)
    assert a[24:45] != b[24:45]


def test_provision_blob_round_trips():
    blob = mc.seal_provision(APP, MAC, NET, sleepy=True)
    net, sleepy = mc.open_provision(APP, MAC, blob)
    assert net == NET
    assert sleepy is True


def test_provision_blob_rejects_a_wrong_app_key():
    blob = mc.seal_provision(APP, MAC, NET, sleepy=False)
    with pytest.raises(mc.MeshAuthError):
        mc.open_provision(bytes(16), MAC, blob)
