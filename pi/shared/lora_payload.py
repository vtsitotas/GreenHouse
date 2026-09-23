"""Compact LoRaWAN payloads (spec 2026-09-14 §What to actually send).

Every message fits the EU868 DR0/SF12 cap of 51 bytes. Values are scaled
x10 integers, never raw floats -- a float is 4 bytes and carries precision
the sensors do not have.
"""
import re
import struct
from dataclasses import dataclass

MAX_PAYLOAD = 51
PORT_SUMMARY, PORT_ALERT, PORT_COMMAND = 1, 2, 10
VERSION = 1
_ZONE = struct.Struct('>BhHH')          # 7 bytes
_ZONES_PER_MSG = (MAX_PAYLOAD - 2) // _ZONE.size   # 7
_TEMP_NONE, _U16_NONE = 0x7FFF, 0xFFFF
_SEVERITIES = ('info', 'warning', 'critical')
_TEXT_MAX = 40


@dataclass
class ZoneSummary:
    zone: int
    temperature: float | None
    humidity: float | None
    soil: float | None


def zone_number(zone_name: str):
    m = re.fullmatch(r'zone(\d{1,3})', zone_name or '')
    n = int(m.group(1)) if m else None
    return n if n is not None and n <= 255 else None


def _i16(v):
    return _TEMP_NONE if v is None else max(-32767, min(32766, round(v * 10)))


def _u16(v):
    return _U16_NONE if v is None else max(0, min(65534, round(v * 10)))


def encode_summaries(zones):
    out = []
    for i in range(0, len(zones), _ZONES_PER_MSG):
        chunk = zones[i:i + _ZONES_PER_MSG]
        body = b''.join(_ZONE.pack(z.zone, _i16(z.temperature), _u16(z.humidity), _u16(z.soil))
                        for z in chunk)
        out.append(bytes([VERSION, len(chunk)]) + body)
    return out


def decode_summary(data: bytes):
    if len(data) < 2 or data[0] != VERSION:
        raise ValueError('unsupported summary version')
    n = data[1]
    if len(data) != 2 + n * _ZONE.size:
        raise ValueError('summary length mismatch')
    out = []
    for i in range(n):
        zone, t, h, s = _ZONE.unpack_from(data, 2 + i * _ZONE.size)
        out.append(ZoneSummary(zone,
                               None if t == _TEMP_NONE else t / 10,
                               None if h == _U16_NONE else h / 10,
                               None if s == _U16_NONE else s / 10))
    return out


def _text(s: str) -> bytes:
    return s.encode('ascii', 'replace')[:_TEXT_MAX]


def encode_alert(severity: str, rule_id: str) -> bytes:
    return bytes([VERSION, _SEVERITIES.index(severity) if severity in _SEVERITIES else 1]) + _text(rule_id)


def decode_alert(data: bytes) -> dict:
    if len(data) < 2 or data[0] != VERSION or data[1] >= len(_SEVERITIES):
        raise ValueError('unsupported alert')
    return {'severity': _SEVERITIES[data[1]], 'rule_id': data[2:].decode('ascii', 'replace')}


def encode_command(actuator: str, on: bool) -> bytes:
    name = _text(actuator)
    if not name:
        raise ValueError('actuator name must not be empty')
    return bytes([VERSION, 1 if on else 0]) + name


def decode_command(data: bytes):
    if len(data) < 3 or data[0] != VERSION or data[1] > 1:
        raise ValueError('unsupported command')
    return data[2:].decode('ascii', 'replace'), data[1] == 1
