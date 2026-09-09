# pi/shared/mesh_crypto.py
"""AES-GCM / AES-CMAC for the v2 mesh packet.

Two independent keys, per the design spec:
  AppKey — per sensor, seals the body end to end; only this Pi holds it.
  NetKey — network-wide, authenticates the header so relays can drop garbage
           cheaply. Never protects sensor data.
"""
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import cmac
from cryptography.hazmat.primitives.ciphers import algorithms
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import mesh_packet as mp

# Domain separator so a provisioning blob can never be replayed as a reading.
PROVISION_AAD = b'greenhouse-provision-v1'


class MeshFormatError(ValueError):
    """Packet is the wrong length or carries an unknown magic."""


class MeshAuthError(ValueError):
    """Authentication failed: wrong key, tampering, or corruption."""


def _nettag(raw: bytes, net_key: bytes) -> bytes:
    c = cmac.CMAC(algorithms.AES(net_key))
    c.update(mp.header_aad(raw))
    return c.finalize()[:mp.NETTAG_LEN]


def seal_packet(app_key: bytes, net_key: bytes, origin_mac: bytes, seq: int,
                boot_count: int, flags: int, rank: int, ttl: int,
                body: bytes) -> bytes:
    if len(body) != mp.BODY_LEN:
        raise MeshFormatError(f'body must be {mp.BODY_LEN} bytes')
    header = mp.pack_header(origin_mac, seq, boot_count, flags, rank, ttl)
    nonce = mp.build_nonce(origin_mac, boot_count, seq)
    sealed = AESGCM(app_key).encrypt(nonce, body, mp.header_aad(header))
    return header + _nettag(header, net_key) + sealed


def verify_nettag(raw: bytes, net_key: bytes) -> bool:
    if len(raw) != mp.PACKET_LEN:
        return False
    expected = _nettag(raw, net_key)
    actual = raw[mp.HEADER_LEN:mp.HEADER_LEN + mp.NETTAG_LEN]
    # CMAC output is already a MAC; a constant-time compare still costs nothing.
    return len(expected) == len(actual) and \
        sum(x ^ y for x, y in zip(expected, actual)) == 0


def open_packet(raw: bytes, app_key: bytes) -> mp.SensorBody:
    if len(raw) != mp.PACKET_LEN:
        raise MeshFormatError(f'packet must be {mp.PACKET_LEN} bytes, got {len(raw)}')
    h = mp.parse_header(raw)                       # raises on a bad magic
    nonce = mp.build_nonce(h.origin_mac, h.boot_count, h.seq)
    sealed = raw[mp.HEADER_LEN + mp.NETTAG_LEN:]
    try:
        body = AESGCM(app_key).decrypt(nonce, sealed, mp.header_aad(raw))
    except (InvalidSignature, InvalidTag) as exc:
        raise MeshAuthError('packet failed authentication') from exc
    return mp.parse_body(body)


def _provision_nonce(mac: bytes) -> bytes:
    # Distinct nonce domain from readings: boot_count and seq are both zero here,
    # and the AAD differs, so a reading can never be confused with a blob.
    return mac + b'\x00' * 6


def seal_provision(app_key: bytes, mac: bytes, net_key: bytes, sleepy: bool) -> bytes:
    plain = net_key + bytes([1 if sleepy else 0])
    return AESGCM(app_key).encrypt(_provision_nonce(mac), plain, PROVISION_AAD)


def open_provision(app_key: bytes, mac: bytes, blob: bytes) -> tuple:
    try:
        plain = AESGCM(app_key).decrypt(_provision_nonce(mac), blob, PROVISION_AAD)
    except (InvalidSignature, InvalidTag) as exc:
        raise MeshAuthError('provision blob failed authentication') from exc
    return plain[:16], plain[16] == 1
