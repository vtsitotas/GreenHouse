# pi/shared/mesh_packet.py
"""Byte-level layout of the v2 mesh packet.

Layout is frozen by 2026-09-09-unlimited-sensors-app-onboarding-design.md and is
mirrored byte for byte by firmware/libraries/GreenhouseMesh/mesh_packet.h. The
host harness in firmware/test/host cross-checks the two; change neither alone.
"""
import struct
from typing import NamedTuple

MESH_MAGIC_V2 = 0x48

HEADER_LEN = 16
NETTAG_LEN = 8
BODY_LEN = 21
APPTAG_LEN = 16
PACKET_LEN = HEADER_LEN + NETTAG_LEN + BODY_LEN + APPTAG_LEN  # 61

# ttl is the last header byte and is rewritten at every hop, so it is excluded
# from both the GCM AAD and the nettag CMAC input.
AAD_LEN = HEADER_LEN - 1

_HEADER = struct.Struct('<B6sHIBBB')
_BODY = struct.Struct('<fffH6sb')


class MeshHeader(NamedTuple):
    magic: int
    origin_mac: bytes
    seq: int
    boot_count: int
    flags: int
    rank: int
    ttl: int


class SensorBody(NamedTuple):
    temperature: float
    humidity: float
    soil_moisture: float
    battery_mv: int
    parent_mac: bytes
    parent_rssi: int


def pack_header(origin_mac: bytes, seq: int, boot_count: int,
                flags: int, rank: int, ttl: int) -> bytes:
    return _HEADER.pack(MESH_MAGIC_V2, origin_mac, seq, boot_count, flags, rank, ttl)


def parse_header(raw: bytes) -> MeshHeader:
    if len(raw) < HEADER_LEN:
        raise ValueError(f'header needs {HEADER_LEN} bytes, got {len(raw)}')
    h = MeshHeader(*_HEADER.unpack(raw[:HEADER_LEN]))
    if h.magic != MESH_MAGIC_V2:
        raise ValueError(f'bad magic 0x{h.magic:02x}, expected 0x{MESH_MAGIC_V2:02x}')
    return h


def header_aad(raw: bytes) -> bytes:
    return raw[:AAD_LEN]


def build_nonce(origin_mac: bytes, boot_count: int, seq: int) -> bytes:
    return origin_mac + struct.pack('<IH', boot_count, seq)


def pack_body(temperature: float, humidity: float, soil_moisture: float,
              battery_mv: int, parent_mac: bytes, parent_rssi: int) -> bytes:
    return _BODY.pack(temperature, humidity, soil_moisture,
                      battery_mv, parent_mac, parent_rssi)


def parse_body(raw: bytes) -> SensorBody:
    if len(raw) != BODY_LEN:
        raise ValueError(f'body must be {BODY_LEN} bytes, got {len(raw)}')
    return SensorBody(*_BODY.unpack(raw))
