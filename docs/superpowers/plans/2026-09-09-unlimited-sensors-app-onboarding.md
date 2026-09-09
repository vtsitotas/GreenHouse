# Unlimited Sensors + In-App Sensor Onboarding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the 8-device ceiling and let the owner add any number of sensors by scanning a QR code in the app, with no reflash and no per-device firmware work.

**Architecture:** Stop using ESP-NOW's encrypted-peer mechanism (the source of the 7-peer cap) and move confidentiality to the application layer: AES-GCM sensor→Pi under a per-device AppKey, plus an AES-CMAC tag under a network-wide NetKey so relays can cheaply reject garbage and strangers cannot inject routing state. Every node then registers exactly two ESP-NOW peers — broadcast and its current parent — regardless of fleet size. The trust store lives only on the Pi, which is also the only place packets are decrypted; the bridge becomes a keyless radio↔UART pipe.

**Tech Stack:** ESP32-C3 / Arduino (mbedTLS for AES-GCM and CMAC, NVS for key storage), Python 3.11 on the Pi (`cryptography` via apt), Flutter/Riverpod app, pytest + flutter test + a new host-compiled C++ layout harness in CI.

**Spec:** `docs/superpowers/specs/2026-09-09-unlimited-sensors-app-onboarding-design.md`

## Global Constraints

- **Packet layout is frozen by the spec and every implementation must match it byte for byte:** header 16 B, nettag 8 B, ciphertext 21 B, apptag 16 B, total 61 B.
- **Header layout, little-endian, packed:** `magic`(1) at offset 0, `origin_mac`(6) at 1, `seq`(uint16) at 7, `boot_count`(uint32) at 9, `flags`(1) at 13, `rank`(1) at 14, `ttl`(1) at 15.
- **AAD for AES-GCM and the input to the nettag CMAC are both `packet[0:15]`** — the header excluding the mutable `ttl` byte at offset 15.
- **Nonce is exactly `origin_mac(6) ‖ boot_count(4, LE) ‖ seq(2, LE)` = 12 bytes.** Never derived any other way.
- **`MESH_MAGIC_V2 = 0x48`** (`'H'`), distinct from today's `0x47` so old and new packets can never be confused.
- **Body layout, little-endian, packed:** `temperature` f32 at 0, `humidity` f32 at 4, `soil_moisture` f32 at 8, `battery_mv` uint16 at 12, `parent_mac`(6) at 14, `parent_rssi` int8 at 20 = 21 B.
- **`boot_count` lives in NVS flash and increments on cold boot only** — never on a timer wake. Nonce reuse under AES-GCM leaks the authentication subkey; see the spec's §Nonce construction.
- **AppKeys never leave the Pi and the sensor.** They must never be written to the bridge, never logged, and never returned by any HTTP endpoint.
- **The NetKey is held by the bridge in RAM only** — re-sent by the Pi on every serial connect, never written to bridge NVS.
- **Pi Python dependencies install via apt, not pip** (`python3-cryptography`). The Pi Zero W is ARMv6; pip-building `cryptography` requires Rust and will fail or thrash the board, the same class of failure as the 2026-07-10 `firebase-admin` incident.
- **Trust store file** is `/etc/greenhouse/nodes.json`, mode `0600`, owned `pi:pi` — root ownership silently breaks services running as `pi`, as it did for the Firebase key on 2026-07-10.

---

### Task 1: Packet layout and nonce construction (Python)

The byte-level contract every other task depends on. Pure functions, no I/O, no crypto yet.

**Files:**
- Create: `pi/shared/mesh_packet.py`
- Test: `pi/tests/test_mesh_packet.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MESH_MAGIC_V2`, `HEADER_LEN=16`, `NETTAG_LEN=8`, `BODY_LEN=21`, `APPTAG_LEN=16`, `PACKET_LEN=61`, `MeshHeader` (NamedTuple: `magic:int, origin_mac:bytes, seq:int, boot_count:int, flags:int, rank:int, ttl:int`), `SensorBody` (NamedTuple: `temperature:float, humidity:float, soil_moisture:float, battery_mv:int, parent_mac:bytes, parent_rssi:int`), `pack_header(...) -> bytes`, `parse_header(raw:bytes) -> MeshHeader`, `header_aad(raw:bytes) -> bytes`, `build_nonce(origin_mac:bytes, boot_count:int, seq:int) -> bytes`, `pack_body(...) -> bytes`, `parse_body(raw:bytes) -> SensorBody`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pi/tests/test_mesh_packet.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mesh_packet'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest pi/tests/test_mesh_packet.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add pi/shared/mesh_packet.py pi/tests/test_mesh_packet.py
git commit -m "feat: v2 mesh packet layout and nonce construction"
```

---

### Task 2: Seal, open, and nettag verification (Python)

**Files:**
- Create: `pi/shared/mesh_crypto.py`
- Test: `pi/tests/test_mesh_crypto.py`
- Modify: `.github/workflows/ci.yml:32` (add `cryptography` to the pip line)

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces: `seal_packet(app_key, net_key, origin_mac, seq, boot_count, flags, rank, ttl, body) -> bytes` (returns the full 61-byte packet), `open_packet(raw, app_key) -> SensorBody`, `verify_nettag(raw, net_key) -> bool`, `seal_provision(app_key, mac, net_key, sleepy) -> bytes`, `open_provision(app_key, mac, blob) -> tuple[bytes, bool]`, and the exceptions `MeshAuthError`, `MeshFormatError`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pi/tests/test_mesh_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mesh_crypto'`

- [ ] **Step 3: Write minimal implementation**

```python
# pi/shared/mesh_crypto.py
"""AES-GCM / AES-CMAC for the v2 mesh packet.

Two independent keys, per the design spec:
  AppKey — per sensor, seals the body end to end; only this Pi holds it.
  NetKey — network-wide, authenticates the header so relays can drop garbage
           cheaply. Never protects sensor data.
"""
from cryptography.exceptions import InvalidSignature
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
    except InvalidSignature as exc:
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
    except InvalidSignature as exc:
        raise MeshAuthError('provision blob failed authentication') from exc
    return plain[:16], plain[16] == 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest pi/tests/test_mesh_crypto.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Add the dependency to CI**

In `.github/workflows/ci.yml`, append `cryptography` to the existing `pip install` line (line 32) so it reads `... "Pillow==10.3.0" cryptography`, and add a comment above it matching the file's existing style:

```yaml
          # cryptography: real dependency of pi/shared/mesh_crypto.py, installed
          # on the real Pi via apt as python3-cryptography (install.sh) — the Pi
          # Zero W is ARMv6 and pip-building it would need Rust.
```

- [ ] **Step 6: Run the whole Pi suite and commit**

Run: `python -m pytest pi/tests/ -v`
Expected: PASS, no regressions

```bash
git add pi/shared/mesh_crypto.py pi/tests/test_mesh_crypto.py .github/workflows/ci.yml
git commit -m "feat: AES-GCM sealing and CMAC header authentication for mesh packets"
```

---

### Task 3: The trust store (Python)

**Files:**
- Create: `pi/shared/nodes.py`
- Test: `pi/tests/test_nodes.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Node` (dataclass: `mac:str, app_key:bytes, zone:str, name:str, sleepy:bool`), `NODES_PATH`, `load(path) -> dict[str, Node]`, `save(nodes, path) -> None`, `add(node, path) -> None`, `remove(mac, path) -> bool`, `normalise_mac(value:str) -> str`, `NodeStoreError`. Keys in the returned dict are normalised MACs (12 uppercase hex, no separators).

- [ ] **Step 1: Write the failing test**

```python
# pi/tests/test_nodes.py
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))

import nodes


@pytest.fixture
def store(tmp_path):
    return str(tmp_path / 'nodes.json')


def _node(mac='20:6e:f1:6c:9d:b0', zone='zone2'):
    return nodes.Node(mac=nodes.normalise_mac(mac), app_key=bytes(range(16)),
                      zone=zone, name='Tomato bed', sleepy=True)


def test_normalise_mac_strips_separators_and_uppercases():
    assert nodes.normalise_mac('20:6e:f1:6c:9d:b0') == '206EF16C9DB0'
    assert nodes.normalise_mac('206ef16c9db0') == '206EF16C9DB0'


def test_normalise_mac_rejects_rubbish():
    for bad in ('', 'zz', '206EF16C9DB', '206EF16C9DB0FF'):
        with pytest.raises(ValueError):
            nodes.normalise_mac(bad)


def test_missing_file_loads_as_empty_not_an_error(store):
    assert nodes.load(store) == {}


def test_add_then_load_round_trips_including_the_key(store):
    nodes.add(_node(), store)
    loaded = nodes.load(store)
    assert set(loaded) == {'206EF16C9DB0'}
    assert loaded['206EF16C9DB0'].app_key == bytes(range(16))
    assert loaded['206EF16C9DB0'].zone == 'zone2'
    assert loaded['206EF16C9DB0'].sleepy is True


def test_adding_the_same_mac_twice_replaces_rather_than_duplicates(store):
    nodes.add(_node(zone='zone2'), store)
    nodes.add(_node(zone='zone9'), store)
    loaded = nodes.load(store)
    assert len(loaded) == 1
    assert loaded['206EF16C9DB0'].zone == 'zone9'


def test_remove_reports_whether_it_removed_anything(store):
    nodes.add(_node(), store)
    assert nodes.remove('206EF16C9DB0', store) is True
    assert nodes.remove('206EF16C9DB0', store) is False
    assert nodes.load(store) == {}


def test_store_is_written_0600_because_it_holds_every_app_key(store):
    nodes.add(_node(), store)
    assert stat.S_IMODE(os.stat(store).st_mode) == 0o600


def test_corrupt_store_raises_loudly_rather_than_silently_losing_sensors(store):
    with open(store, 'w') as fh:
        fh.write('{not json')
    with pytest.raises(nodes.NodeStoreError):
        nodes.load(store)


def test_entry_with_a_bad_key_length_is_rejected(store):
    with open(store, 'w') as fh:
        json.dump({'version': 1, 'nodes': [
            {'mac': '206EF16C9DB0', 'app_key': 'aabb',
             'zone': 'zone2', 'name': 'x', 'sleepy': False}]}, fh)
    with pytest.raises(nodes.NodeStoreError):
        nodes.load(store)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pi/tests/test_nodes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nodes'`

- [ ] **Step 3: Write minimal implementation**

```python
# pi/shared/nodes.py
"""The sensor trust store — the single source of truth for who is in the mesh.

Holds every sensor's AppKey, so it is written 0600 and never served over HTTP.
Nothing else in the system stores keys: the bridge is deliberately keyless.
"""
import json
import os
import re
import tempfile
from dataclasses import dataclass

NODES_PATH = '/etc/greenhouse/nodes.json'

_MAC_RE = re.compile(r'^[0-9A-F]{12}$')
APP_KEY_LEN = 16


class NodeStoreError(RuntimeError):
    """The store exists but cannot be trusted — refuse rather than drop sensors."""


@dataclass
class Node:
    mac: str
    app_key: bytes
    zone: str
    name: str
    sleepy: bool


def normalise_mac(value: str) -> str:
    mac = re.sub(r'[^0-9A-Fa-f]', '', value or '').upper()
    if not _MAC_RE.match(mac):
        raise ValueError(f'not a MAC address: {value!r}')
    return mac


def load(path: str = NODES_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            raw = json.load(fh)
        out = {}
        for entry in raw['nodes']:
            key = bytes.fromhex(entry['app_key'])
            if len(key) != APP_KEY_LEN:
                raise ValueError(f'app_key for {entry["mac"]} is {len(key)} bytes')
            mac = normalise_mac(entry['mac'])
            out[mac] = Node(mac, key, entry['zone'], entry['name'],
                            bool(entry['sleepy']))
        return out
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise NodeStoreError(f'{path} is unreadable or malformed: {exc}') from exc


def save(store: dict, path: str = NODES_PATH) -> None:
    payload = {'version': 1, 'nodes': [
        {'mac': n.mac, 'app_key': n.app_key.hex(), 'zone': n.zone,
         'name': n.name, 'sleepy': n.sleepy}
        for n in store.values()]}
    directory = os.path.dirname(path) or '.'
    # Write-then-rename so a power cut mid-write cannot truncate the store.
    fd, tmp = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(payload, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def add(node: Node, path: str = NODES_PATH) -> None:
    store = load(path)
    store[normalise_mac(node.mac)] = node
    save(store, path)


def remove(mac: str, path: str = NODES_PATH) -> bool:
    store = load(path)
    if store.pop(normalise_mac(mac), None) is None:
        return False
    save(store, path)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest pi/tests/test_nodes.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add pi/shared/nodes.py pi/tests/test_nodes.py
git commit -m "feat: Pi-side sensor trust store with atomic 0600 writes"
```

---

### Task 4: Firmware packet layout + host cross-check harness

The firmware's layout must match Task 1's byte for byte. This task builds the C++ side as a
dependency-free header and a host harness that proves the match in CI — the single most
valuable test in this plan, because a layout mismatch otherwise appears only as a sensor that
silently never reports.

**Files:**
- Create: `firmware/libraries/GreenhouseMesh/mesh_packet.h`
- Create: `firmware/test/host/layout_vectors.cpp`
- Create: `firmware/test/host/Makefile`
- Test: `pi/tests/test_firmware_layout.py`
- Modify: `.github/workflows/ci.yml` (new job)

**Interfaces:**
- Consumes: Task 1's layout constants (as the reference to match).
- Produces: C++ `MESH_MAGIC_V2`, `MESH_HEADER_LEN`, `MESH_NETTAG_LEN`, `MESH_BODY_LEN`, `MESH_APPTAG_LEN`, `MESH_PACKET_LEN`, `MESH_AAD_LEN`, and the functions `meshPackHeader(uint8_t* out, const uint8_t* originMac, uint16_t seq, uint32_t bootCount, uint8_t flags, uint8_t rank, uint8_t ttl)`, `meshPackBody(uint8_t* out, float t, float h, float s, uint16_t batteryMv, const uint8_t* parentMac, int8_t rssi)`, `meshBuildNonce(uint8_t* out12, const uint8_t* originMac, uint32_t bootCount, uint16_t seq)`.

- [ ] **Step 1: Write the failing test**

```python
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
    if subprocess.call(['g++', '--version'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pi/tests/test_firmware_layout.py -v`
Expected: FAIL — `make` cannot find the directory

- [ ] **Step 3: Write the firmware layout header**

```cpp
// firmware/libraries/GreenhouseMesh/mesh_packet.h
#pragma once
// ── v2 mesh packet layout ─────────────────────────────────────────────────────
// Mirrors pi/shared/mesh_packet.py byte for byte; firmware/test/host proves it
// in CI. Deliberately free of Arduino/mbedTLS includes so it compiles on a host.
//
//   [ header 16 ][ nettag 8 ][ ciphertext 21 ][ apptag 16 ] = 61 bytes
//
// ttl is the last header byte and is rewritten at every hop, so both the GCM AAD
// and the nettag CMAC cover only the first 15 bytes.

#include <stdint.h>
#include <string.h>

#define MESH_MAGIC_V2     0x48
#define MESH_HEADER_LEN   16
#define MESH_NETTAG_LEN   8
#define MESH_BODY_LEN     21
#define MESH_APPTAG_LEN   16
#define MESH_PACKET_LEN   (MESH_HEADER_LEN + MESH_NETTAG_LEN + \
                           MESH_BODY_LEN + MESH_APPTAG_LEN)
#define MESH_AAD_LEN      (MESH_HEADER_LEN - 1)

// Explicit little-endian writers: never memcpy a struct onto the wire. Struct
// padding and host endianness are exactly how a layout silently diverges.
static inline void meshPutU16(uint8_t* p, uint16_t v) {
  p[0] = (uint8_t)(v & 0xFF); p[1] = (uint8_t)((v >> 8) & 0xFF);
}

static inline void meshPutU32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v & 0xFF);         p[1] = (uint8_t)((v >> 8) & 0xFF);
  p[2] = (uint8_t)((v >> 16) & 0xFF); p[3] = (uint8_t)((v >> 24) & 0xFF);
}

static inline void meshPutF32(uint8_t* p, float v) {
  uint32_t bits; memcpy(&bits, &v, 4); meshPutU32(p, bits);
}

static inline void meshPackHeader(uint8_t* out, const uint8_t* originMac,
                                  uint16_t seq, uint32_t bootCount,
                                  uint8_t flags, uint8_t rank, uint8_t ttl) {
  out[0] = MESH_MAGIC_V2;
  memcpy(out + 1, originMac, 6);
  meshPutU16(out + 7, seq);
  meshPutU32(out + 9, bootCount);
  out[13] = flags;
  out[14] = rank;
  out[15] = ttl;
}

static inline void meshPackBody(uint8_t* out, float t, float h, float s,
                                uint16_t batteryMv, const uint8_t* parentMac,
                                int8_t rssi) {
  meshPutF32(out + 0, t);
  meshPutF32(out + 4, h);
  meshPutF32(out + 8, s);
  meshPutU16(out + 12, batteryMv);
  memcpy(out + 14, parentMac, 6);
  out[20] = (uint8_t)rssi;
}

static inline void meshBuildNonce(uint8_t* out12, const uint8_t* originMac,
                                  uint32_t bootCount, uint16_t seq) {
  memcpy(out12, originMac, 6);
  meshPutU32(out12 + 6, bootCount);
  meshPutU16(out12 + 10, seq);
}
```

- [ ] **Step 4: Write the harness and its Makefile**

```cpp
// firmware/test/host/layout_vectors.cpp
// Prints the firmware's own byte layout so pytest can diff it against the Pi's.
#include <cstdio>
#include "../../libraries/GreenhouseMesh/mesh_packet.h"

static void printHex(const char* label, const uint8_t* buf, int len) {
  printf("%s=", label);
  for (int i = 0; i < len; i++) printf("%02x", buf[i]);
  printf("\n");
}

int main() {
  const uint8_t mac[6]    = { 0x20, 0x6E, 0xF1, 0x6C, 0x9D, 0xB0 };
  const uint8_t parent[6] = { 0x20, 0x6E, 0xF1, 0x6C, 0xBE, 0x80 };

  uint8_t header[MESH_HEADER_LEN];
  meshPackHeader(header, mac, 0x1234, 0x0A0B0C0D, 0x01, 2, 4);
  printHex("header", header, MESH_HEADER_LEN);

  uint8_t body[MESH_BODY_LEN];
  meshPackBody(body, 21.5f, 60.25f, 42.0f, 3700, parent, -67);
  printHex("body", body, MESH_BODY_LEN);

  uint8_t nonce[12];
  meshBuildNonce(nonce, mac, 0x0A0B0C0D, 0x1234);
  printHex("nonce", nonce, 12);

  printf("header_len=%d\n", MESH_HEADER_LEN);
  printf("body_len=%d\n",   MESH_BODY_LEN);
  printf("packet_len=%d\n", MESH_PACKET_LEN);
  printf("aad_len=%d\n",    MESH_AAD_LEN);
  printf("magic=%d\n",      MESH_MAGIC_V2);
  return 0;
}
```

```makefile
# firmware/test/host/Makefile
CXXFLAGS = -std=c++11 -Wall -Wextra -Werror -O1

layout_vectors: layout_vectors.cpp ../../libraries/GreenhouseMesh/mesh_packet.h
	$(CXX) $(CXXFLAGS) -o $@ layout_vectors.cpp

.PHONY: clean
clean:
	rm -f layout_vectors
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest pi/tests/test_firmware_layout.py -v`
Expected: PASS (4 tests), or SKIP if `g++` is absent locally

- [ ] **Step 6: Make CI build the harness**

Add to `.github/workflows/ci.yml` under `jobs:` (CI runs on ubuntu-latest, which has g++, so the tests above will run rather than skip — the `pi-tests` job picks them up automatically). Add a `.gitignore` entry so the built binary is never committed:

```bash
echo "firmware/test/host/layout_vectors" >> .gitignore
```

- [ ] **Step 7: Commit**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_packet.h firmware/test/host/ \
        pi/tests/test_firmware_layout.py .gitignore
git commit -m "feat: firmware packet layout with a host harness cross-checking the Pi"
```

---

### Task 5: Firmware key store and boot counter (NVS)

**Files:**
- Create: `firmware/libraries/GreenhouseMesh/mesh_store.h`

**Interfaces:**
- Consumes: nothing.
- Produces: `meshStoreBegin()`, `meshStoreAppKey(uint8_t* out16) -> bool`, `meshStoreSetAppKey(const uint8_t*)`, `meshStoreNetKey(uint8_t* out16) -> bool`, `meshStoreSetNetKey(const uint8_t*)`, `meshStoreIsProvisioned() -> bool`, `meshStoreSleepy() -> bool`, `meshStoreSetSleepy(bool)`, `meshStoreBumpBootCount() -> uint32_t`, `meshStoreBootCount() -> uint32_t`, `meshStoreClearProvisioning()`.

- [ ] **Step 1: Write the implementation**

There is no host test for this task — it is a thin wrapper over the ESP32 `Preferences`
API with no logic worth mocking. It is verified on hardware in Task 16.

```cpp
// firmware/libraries/GreenhouseMesh/mesh_store.h
#pragma once
// ── Persistent per-node identity ──────────────────────────────────────────────
// AppKey  — this node's own key, written once at provisioning-tool time.
// NetKey  — learned over the air during enrolment; its presence IS the
//           "provisioned" flag.
// boot    — monotonic cold-boot counter feeding the AES-GCM nonce. seq lives in
//           RTC memory and survives sleep but NOT power loss; without this
//           counter a power cut would reuse a nonce, which in GCM leaks the
//           authentication subkey and lets an attacker forge readings. Bumped on
//           cold boot ONLY, so a node on the 15-minute cycle writes flash only
//           when it actually loses power.

#include <Preferences.h>
#include <stdint.h>
#include <string.h>

static Preferences meshPrefs;

static void meshStoreBegin() { meshPrefs.begin("ghmesh", false); }

static bool meshStoreAppKey(uint8_t* out16) {
  return meshPrefs.getBytes("appkey", out16, 16) == 16;
}

static void meshStoreSetAppKey(const uint8_t* key16) {
  meshPrefs.putBytes("appkey", key16, 16);
}

static bool meshStoreNetKey(uint8_t* out16) {
  return meshPrefs.getBytes("netkey", out16, 16) == 16;
}

static void meshStoreSetNetKey(const uint8_t* key16) {
  meshPrefs.putBytes("netkey", key16, 16);
}

static bool meshStoreIsProvisioned() {
  uint8_t tmp[16];
  return meshStoreNetKey(tmp);
}

static bool meshStoreSleepy()          { return meshPrefs.getBool("sleepy", false); }
static void meshStoreSetSleepy(bool v) { meshPrefs.putBool("sleepy", v); }
static uint32_t meshStoreBootCount()   { return meshPrefs.getUInt("boot", 0); }

static uint32_t meshStoreBumpBootCount() {
  uint32_t next = meshStoreBootCount() + 1;
  meshPrefs.putUInt("boot", next);
  return next;
}

// Factory reset: forget the network but keep our own identity, so the node can
// simply be re-enrolled from the app without a reflash.
static void meshStoreClearProvisioning() {
  meshPrefs.remove("netkey");
  meshPrefs.remove("sleepy");
}
```

- [ ] **Step 2: Commit**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_store.h
git commit -m "feat: NVS-backed key store and cold-boot counter for mesh nodes"
```

---

### Task 6: Firmware crypto wrappers + on-device vector selftest

**Files:**
- Create: `firmware/libraries/GreenhouseMesh/mesh_crypto.h`
- Create: `firmware/crypto_selftest/crypto_selftest.ino`
- Test: `pi/tests/test_firmware_vectors.py`
- Create: `firmware/test/vectors/device_vectors.txt`

**Interfaces:**
- Consumes: Task 4's layout, Task 5's store.
- Produces: `meshSeal(uint8_t* packet, const uint8_t* appKey, const uint8_t* netKey, const uint8_t* originMac, uint16_t seq, uint32_t bootCount, uint8_t flags, uint8_t rank, uint8_t ttl, const uint8_t* body) -> bool` (writes `MESH_PACKET_LEN` bytes), `meshVerifyNettag(const uint8_t* packet, const uint8_t* netKey) -> bool`, `meshOpenProvision(const uint8_t* appKey, const uint8_t* mac, const uint8_t* blob, int blobLen, uint8_t* outNetKey16, bool* outSleepy) -> bool`.

- [ ] **Step 1: Write the implementation**

```cpp
// firmware/libraries/GreenhouseMesh/mesh_crypto.h
#pragma once
// AES-GCM (body, AppKey) + AES-CMAC (header, NetKey) over mbedTLS, which ships
// with the ESP32 Arduino core and uses the C3's hardware AES.
#include <mbedtls/cipher.h>
#include <mbedtls/gcm.h>
#include <string.h>

#include "mesh_packet.h"

#define MESH_PROVISION_AAD     "greenhouse-provision-v1"
#define MESH_PROVISION_AAD_LEN 23
#define MESH_PROVISION_LEN     33   // 16-byte NetKey + 1 flags byte + 16-byte tag

static bool meshCmacTruncated(const uint8_t* key, const uint8_t* in, size_t len,
                              uint8_t* out8) {
  const mbedtls_cipher_info_t* info =
      mbedtls_cipher_info_from_type(MBEDTLS_CIPHER_AES_128_ECB);
  if (!info) return false;
  uint8_t full[16];
  if (mbedtls_cipher_cmac(info, key, 128, in, len, full) != 0) return false;
  memcpy(out8, full, MESH_NETTAG_LEN);
  return true;
}

static bool meshSeal(uint8_t* packet, const uint8_t* appKey, const uint8_t* netKey,
                     const uint8_t* originMac, uint16_t seq, uint32_t bootCount,
                     uint8_t flags, uint8_t rank, uint8_t ttl, const uint8_t* body) {
  meshPackHeader(packet, originMac, seq, bootCount, flags, rank, ttl);
  if (!meshCmacTruncated(netKey, packet, MESH_AAD_LEN, packet + MESH_HEADER_LEN))
    return false;

  uint8_t nonce[12];
  meshBuildNonce(nonce, originMac, bootCount, seq);

  uint8_t* ct  = packet + MESH_HEADER_LEN + MESH_NETTAG_LEN;
  uint8_t* tag = ct + MESH_BODY_LEN;

  mbedtls_gcm_context gcm;
  mbedtls_gcm_init(&gcm);
  bool ok = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, appKey, 128) == 0 &&
            mbedtls_gcm_crypt_and_tag(&gcm, MBEDTLS_GCM_ENCRYPT, MESH_BODY_LEN,
                                      nonce, sizeof(nonce),
                                      packet, MESH_AAD_LEN,
                                      body, ct, MESH_APPTAG_LEN, tag) == 0;
  mbedtls_gcm_free(&gcm);
  return ok;
}

static bool meshVerifyNettag(const uint8_t* packet, const uint8_t* netKey) {
  uint8_t expected[MESH_NETTAG_LEN];
  if (!meshCmacTruncated(netKey, packet, MESH_AAD_LEN, expected)) return false;
  uint8_t diff = 0;
  for (int i = 0; i < MESH_NETTAG_LEN; i++)
    diff |= expected[i] ^ packet[MESH_HEADER_LEN + i];
  return diff == 0;
}

static bool meshOpenProvision(const uint8_t* appKey, const uint8_t* mac,
                              const uint8_t* blob, int blobLen,
                              uint8_t* outNetKey16, bool* outSleepy) {
  if (blobLen != MESH_PROVISION_LEN) return false;
  uint8_t nonce[12];
  memcpy(nonce, mac, 6);
  memset(nonce + 6, 0, 6);          // distinct nonce domain from readings

  uint8_t plain[17];
  mbedtls_gcm_context gcm;
  mbedtls_gcm_init(&gcm);
  bool ok = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, appKey, 128) == 0 &&
            mbedtls_gcm_auth_decrypt(&gcm, 17, nonce, sizeof(nonce),
                                     (const uint8_t*)MESH_PROVISION_AAD,
                                     MESH_PROVISION_AAD_LEN,
                                     blob + 17, MESH_APPTAG_LEN,
                                     blob, plain) == 0;
  mbedtls_gcm_free(&gcm);
  if (!ok) return false;
  memcpy(outNetKey16, plain, 16);
  *outSleepy = plain[16] == 1;
  return true;
}
```

- [ ] **Step 2: Write the on-device selftest sketch**

The host harness proves layout; this proves the *real mbedTLS output* matches the Pi's real
`cryptography` output. Flash it once whenever the format changes.

```cpp
// firmware/crypto_selftest/crypto_selftest.ino
// Prints one sealed packet built from fixed inputs, for pi/tests/test_firmware_vectors.py.
// Not part of any deployment — a bench tool, flashed on demand.
#include "mesh_crypto.h"

void setup() {
  Serial.begin(115200);
  delay(2000);

  uint8_t appKey[16], netKey[16];
  for (int i = 0; i < 16; i++) { appKey[i] = i; netKey[i] = 16 + i; }
  const uint8_t mac[6]    = { 0x20, 0x6E, 0xF1, 0x6C, 0x9D, 0xB0 };
  const uint8_t parent[6] = { 0x20, 0x6E, 0xF1, 0x6C, 0xBE, 0x80 };

  uint8_t body[MESH_BODY_LEN];
  meshPackBody(body, 21.5f, 60.0f, 42.0f, 3700, parent, -67);

  uint8_t packet[MESH_PACKET_LEN];
  if (!meshSeal(packet, appKey, netKey, mac, 7, 3, 0, 1, 4, body)) {
    Serial.println("packet=FAILED");
    return;
  }
  Serial.print("packet=");
  for (int i = 0; i < MESH_PACKET_LEN; i++) Serial.printf("%02x", packet[i]);
  Serial.println();
}

void loop() {}
```

- [ ] **Step 3: Write the test that pins the device output**

```python
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
```

- [ ] **Step 4: Create the placeholder vector file**

```bash
mkdir -p firmware/test/vectors
printf '# Paste the packet= line printed by firmware/crypto_selftest here.\n' \
  > firmware/test/vectors/device_vectors.txt
```

- [ ] **Step 5: Run the suite**

Run: `python -m pytest pi/tests/test_firmware_vectors.py -v`
Expected: 3 SKIPPED (no captured vectors yet — they arrive in Task 16)

- [ ] **Step 6: Commit**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_crypto.h firmware/crypto_selftest/ \
        firmware/test/vectors/ pi/tests/test_firmware_vectors.py
git commit -m "feat: mbedTLS seal/verify for mesh packets plus an on-device vector selftest"
```

---

### Task 7: Cut TRUSTED_NODES out of the mesh core

The structural change that removes the peer cap. No crypto yet — this task is about identity
and peer registration only, so a failure here is easy to tell apart from a crypto failure.

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_config.h` (delete `TRUSTED_NODES[]`, `TrustedNode`, `TRUSTED_NODE_COUNT`, `MESH_PMK`, `MESH_LMK`; keep all timing/buffer constants and both `static_assert`s)
- Modify: `firmware/libraries/GreenhouseMesh/mesh_node.h:60-73` (state), `:96-121` (trust helpers), `:126-153` (`meshInit`), `:184-200` (parent management), `:414-495` (RTC state), `:511-533` (`meshRelayData`)

**Interfaces:**
- Consumes: Task 5's store.
- Produces: `meshParentMac[6]` and `meshHasParent` replacing `meshParentIdx`; `meshSetParent(const uint8_t* mac, uint8_t rank, uint32_t intervalMs)`; `meshClearParent()`; `meshInit(uint8_t channel)` with the registration loop gone.

- [ ] **Step 1: Replace index-based parent tracking with MAC-based**

In `mesh_node.h`, replace `static int meshParentIdx = -1;` (line 62) with:

```cpp
static uint8_t meshParentMac[6] = { 0 };
static bool    meshHasParent    = false;
```

Replace `meshNeighborLastHeard[TRUSTED_NODE_COUNT]` (line 73) with a fixed ring that no
longer depends on a compile-time fleet size:

```cpp
#define MESH_NEIGHBOR_SLOTS 16
typedef struct { uint8_t mac[6]; uint32_t lastHeardMs; bool used; } MeshNeighbor;
static MeshNeighbor meshNeighbors[MESH_NEIGHBOR_SLOTS];

static void meshNoteNeighbor(const uint8_t* mac, uint32_t nowMs) {
  int oldest = 0;
  for (int i = 0; i < MESH_NEIGHBOR_SLOTS; i++) {
    if (meshNeighbors[i].used && meshMacEqual(meshNeighbors[i].mac, mac)) {
      meshNeighbors[i].lastHeardMs = nowMs;
      return;
    }
    if (!meshNeighbors[i].used) { oldest = i; break; }
    if (meshNeighbors[i].lastHeardMs < meshNeighbors[oldest].lastHeardMs) oldest = i;
  }
  memcpy(meshNeighbors[oldest].mac, mac, 6);
  meshNeighbors[oldest].lastHeardMs = nowMs;
  meshNeighbors[oldest].used = true;
}
```

- [ ] **Step 2: Add dynamic parent-peer registration**

Add above the parent-management section, and call `meshSetParent`/`meshClearParent` from
every site that previously assigned or cleared `meshParentIdx`:

```cpp
// The whole reason the 8-node ceiling is gone: a node registers its CURRENT
// parent and nothing else. Unencrypted peers mean we can still RECEIVE from any
// child without registering it (ESP-NOW registration gates sending, not
// receiving), so a relay never accumulates peers no matter how many children it
// serves.
static uint8_t meshChannel = MESH_FIXED_CHANNEL;

static void meshClearParent() {
  if (meshHasParent) esp_now_del_peer(meshParentMac);
  meshHasParent = false;
  memset(meshParentMac, 0, 6);
  meshMyRank = MESH_RANK_UNROUTED;
  meshParentRank = MESH_RANK_UNROUTED;
}

static bool meshSetParent(const uint8_t* mac, uint8_t rank, uint32_t intervalMs) {
  if (meshHasParent && meshMacEqual(meshParentMac, mac)) {
    meshParentRank = rank;
    meshParentIntervalMs = intervalMs;
    return true;
  }
  meshClearParent();
  esp_now_peer_info_t p = {};
  memcpy(p.peer_addr, mac, 6);
  p.channel = meshChannel;
  p.encrypt = false;                 // confidentiality is app-layer now
  if (esp_now_add_peer(&p) != ESP_OK) {
    Serial.println("[mesh] add_peer failed for new parent");
    return false;
  }
  memcpy(meshParentMac, mac, 6);
  meshHasParent = true;
  meshParentRank = rank;
  meshParentIntervalMs = intervalMs;
  meshMyRank = (uint8_t)(rank + 1);
  meshParentLastHeardMs = millis();
  return true;
}
```

- [ ] **Step 3: Strip meshInit down to the broadcast peer**

Replace the whole body of `meshInit` (lines 130-153) with:

```cpp
static void meshInit(uint8_t channel) {
  WiFi.macAddress(meshSelfMac);
  meshChannel = channel;
  memset(meshNeighbors, 0, sizeof(meshNeighbors));
  memset(meshDedup, 0, sizeof(meshDedup));

  esp_now_peer_info_t bcast = {};
  memcpy(bcast.peer_addr, MESH_BCAST, 6);
  bcast.channel = channel;
  bcast.encrypt = false;
  esp_now_add_peer(&bcast);
  // No per-node registration loop: that loop, with encrypt = true, is what
  // capped the network at 8 devices. Peers are now broadcast + current parent.
}
```

- [ ] **Step 4: Fix the RTC parent hint**

In `MeshRtcState` (line 414) replace `int8_t parentIdx;` with `uint8_t parentMac[6];` and
`bool hasParent;`. In `meshRtcRestore` (line 467) replace the `parentIdx` validity block with:

```cpp
  if (meshRtcState.hasParent &&
      meshRtcState.parentRank != MESH_RANK_UNROUTED) {
    if (meshSetParent(meshRtcState.parentMac, meshRtcState.parentRank,
                      MESH_BEACON_INTERVAL_MAX_MS)) {
      meshParentRssi = -128;   // unknown until a fresh beacon is heard
      return true;
    }
  }
  return false;
```

and in `meshRtcPersist` (line 485) replace the `parentIdx` line with:

```cpp
  meshRtcState.hasParent = meshHasParent;
  memcpy(meshRtcState.parentMac, meshParentMac, 6);
```

- [ ] **Step 5: Replace the relay trust check**

In `meshRelayData` (line 517), delete `if (meshTrustedIndex(srcMac) < 0) return;` and its
now-wrong comment about encrypted peers, replacing it with a nettag check (the NetKey is read
from the store in Task 8; for now leave the length and magic checks and add the `ttl` guard
the spec requires):

```cpp
  if (pkt.ttl > MESH_MAX_TTL) return;   // ttl is outside the nettag, so bound it
```

Delete `meshTrustedIndex()` (lines 96-100) and rewrite `meshIsSelfSleepy()` (lines 112-121)
as `static bool meshIsSelfSleepy() { return meshStoreSleepy(); }`, adding
`#include "mesh_store.h"` at the top of the file.

- [ ] **Step 6: Verify the layout harness still builds and commit**

Run: `python -m pytest pi/tests/ -v`
Expected: PASS, no regressions (this task touches no Python)

```bash
git add firmware/libraries/GreenhouseMesh/mesh_config.h \
        firmware/libraries/GreenhouseMesh/mesh_node.h
git commit -m "refactor: MAC-based parents and dynamic peers, removing the 8-node cap"
```

---

### Task 8: Authenticated beacons and sealed readings

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_node.h` (beacon struct, `meshHandleBeacon`, `meshSendReading`, `meshRelayData`)

**Interfaces:**
- Consumes: Tasks 4-7.
- Produces: `MeshBeacon` gaining `uint8_t tag[MESH_NETTAG_LEN]`; `meshLoadKeys() -> bool` caching AppKey/NetKey from NVS into RAM for the session.

- [ ] **Step 1: Add the beacon tag**

Append to `MeshBeacon` (line 26-39), keeping every existing field in place:

```cpp
  uint8_t  tag[MESH_NETTAG_LEN]; // CMAC-AES(NetKey) over every preceding byte.
                                 // A node without the NetKey cannot forge this,
                                 // so it can never be adopted as a parent —
                                 // this is what replaces the old radio-layer
                                 // rejection of untrusted senders.
} MeshBeacon;                    // 27 bytes
```

- [ ] **Step 2: Cache the keys once per session**

```cpp
static uint8_t meshAppKey[16];
static uint8_t meshNetKey[16];
static bool    meshKeysLoaded = false;

static bool meshLoadKeys() {
  if (meshKeysLoaded) return true;
  meshKeysLoaded = meshStoreAppKey(meshAppKey) && meshStoreNetKey(meshNetKey);
  return meshKeysLoaded;
}
```

- [ ] **Step 3: Sign outgoing beacons and verify incoming ones**

Where the beacon is filled in before broadcast, add as the last step before sending:

```cpp
  meshCmacTruncated(meshNetKey, (const uint8_t*)&b,
                    sizeof(MeshBeacon) - MESH_NETTAG_LEN, b.tag);
```

At the top of `meshHandleBeacon`, before any parent-selection logic:

```cpp
  if (len != (int)sizeof(MeshBeacon)) return;
  uint8_t expect[MESH_NETTAG_LEN];
  if (!meshCmacTruncated(meshNetKey, data,
                         sizeof(MeshBeacon) - MESH_NETTAG_LEN, expect)) return;
  const MeshBeacon* b = (const MeshBeacon*)data;
  uint8_t diff = 0;
  for (int i = 0; i < MESH_NETTAG_LEN; i++) diff |= expect[i] ^ b->tag[i];
  if (diff != 0) { Serial.println("[mesh] beacon failed nettag — ignored"); return; }
```

- [ ] **Step 4: Seal readings instead of sending the struct**

Replace the body of `meshSendReading` so it builds the 61-byte wire packet rather than
transmitting `MeshDataPacket` directly:

```cpp
  uint8_t body[MESH_BODY_LEN];
  meshPackBody(body, r.temperature, r.humidity, r.soil_moisture,
               meshBatteryMv, meshHasParent ? meshParentMac : MESH_BCAST,
               (int8_t)meshParentRssi);

  uint8_t packet[MESH_PACKET_LEN];
  uint8_t ttl = (meshMyRank == MESH_RANK_UNROUTED)
                    ? MESH_MAX_TTL
                    : (uint8_t)(meshMyRank + MESH_TTL_MARGIN);
  if (!meshSeal(packet, meshAppKey, meshNetKey, meshSelfMac, meshDataSeq++,
                meshStoreBootCount(), meshIsSelfSleepy() ? MESH_FLAG_SLEEPY : 0,
                meshMyRank, ttl, body)) {
    Serial.println("[mesh] seal failed — reading dropped");
    return;
  }
```

The RAM buffer (`meshBuf`) now holds sealed 61-byte packets rather than `MeshDataPacket`
structs, so change its type to `uint8_t meshBuf[MESH_DATA_BUFFER_SIZE][MESH_PACKET_LEN]` and
update `meshBufferPush`, `meshRequeueLastReading`, and `MeshRtcState.buf` to match. A buffered
packet keeps its original seq and boot_count, which is exactly what makes the bridge's de-dup
work on a retry.

- [ ] **Step 5: Verify the nettag when relaying**

In `meshRelayData`, after the length check and before de-dup:

```cpp
  if (!meshVerifyNettag(data, meshNetKey)) return;   // stranger's garbage, dropped cheap
```

and change the length check to `if (len != MESH_PACKET_LEN) return;`, the de-dup call to read
`origin_mac` from `data + 1` and `seq` from `data + 7`, and the ttl decrement to operate on
the raw buffer at offset 15.

- [ ] **Step 6: Commit**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_node.h
git commit -m "feat: NetKey-authenticated beacons and AppKey-sealed readings"
```

---

### Task 9: The unprovisioned state machine on edge nodes

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_node.h` (join beacon + provisioning receive)
- Modify: `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino`, `firmware/edge_node_esp32/edge_node_esp32.ino`

**Interfaces:**
- Consumes: Tasks 5, 6, 8.
- Produces: `MeshJoinBeacon` (`magic`, `marker`, `mac[6]`), `MESH_JOIN_MARKER = 0x4A`, `meshSendJoinBeacon()`, `meshHandleProvision(const uint8_t* data, int len) -> bool`, `MESH_JOIN_BEACON_INTERVAL_MS = 3000UL`.

- [ ] **Step 1: Add the join beacon**

```cpp
// Sent only by a node with no NetKey. Deliberately unauthenticated — the node
// has nothing to authenticate with yet — so relays do NOT forward it and it is
// only ever heard by a bridge in direct range. That proximity requirement is
// also the security property: you cannot enrol a sensor you are not standing
// next to. The app tells the owner to hold the sensor near the hub.
#define MESH_JOIN_MARKER            0x4A
#define MESH_JOIN_BEACON_INTERVAL_MS 3000UL

typedef struct __attribute__((packed)) {
  uint8_t magic;    // MESH_MAGIC_V2
  uint8_t marker;   // MESH_JOIN_MARKER — distinguishes this from a real beacon
  uint8_t mac[6];
} MeshJoinBeacon;   // 8 bytes

static void meshSendJoinBeacon() {
  MeshJoinBeacon j = { MESH_MAGIC_V2, MESH_JOIN_MARKER, { 0 } };
  memcpy(j.mac, meshSelfMac, 6);
  esp_now_send(MESH_BCAST, (const uint8_t*)&j, sizeof(j));
}
```

- [ ] **Step 2: Accept a provisioning blob**

```cpp
// The bridge relays an opaque blob the Pi sealed with THIS node's AppKey. The
// bridge cannot read it; only a node holding the matching AppKey can, which is
// what makes over-the-air NetKey delivery safe.
static bool meshHandleProvision(const uint8_t* data, int len) {
  uint8_t netKey[16];
  bool sleepy = false;
  if (!meshStoreAppKey(meshAppKey)) return false;
  if (!meshOpenProvision(meshAppKey, meshSelfMac, data, len, netKey, &sleepy)) {
    Serial.println("[mesh] provision blob rejected — not for us");
    return false;
  }
  meshStoreSetNetKey(netKey);
  meshStoreSetSleepy(sleepy);
  memcpy(meshNetKey, netKey, 16);
  meshKeysLoaded = true;
  Serial.printf("[mesh] provisioned — sleepy=%d\n", sleepy ? 1 : 0);
  return true;
}
```

Route to it from the ESP-NOW receive callback: a frame of exactly `MESH_PROVISION_LEN` bytes
that is not a beacon or a data packet is a provisioning blob.

- [ ] **Step 3: Gate the sketch's main loop on provisioning**

In both edge sketches, in `setup()` after `meshStoreBegin()` and before the normal mesh
bring-up, and in `loop()`:

```cpp
  // Cold boot only: a timer wake keeps its RTC seq, so bumping here would burn
  // flash every 15 minutes for nothing.
  if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER)
    meshStoreBumpBootCount();

  if (!meshStoreIsProvisioned()) {
    // Unenrolled: announce ourselves, never sleep (the owner is standing here
    // with the app open), never route, never read sensors.
    static uint32_t lastJoinMs = 0;
    if (millis() - lastJoinMs >= MESH_JOIN_BEACON_INTERVAL_MS) {
      lastJoinMs = millis();
      meshSendJoinBeacon();
    }
    return;
  }
```

- [ ] **Step 4: Commit**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_node.h \
        firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino \
        firmware/edge_node_esp32/edge_node_esp32.ino
git commit -m "feat: unprovisioned join state and over-the-air NetKey enrolment"
```

---

### Task 10: The bridge becomes a keyless pipe

**Files:**
- Modify: `firmware/bridge_esp32/bridge_esp32.ino`

**Interfaces:**
- Consumes: Tasks 4, 6, 8.
- Produces: upstream UART lines `{"type":"frame","data":"<122 hex>"}` and `{"type":"unenrolled","mac":"<12 hex>"}`; downstream commands `{"type":"netkey","key":"<32 hex>"}` and `{"type":"provision","mac":"<12 hex>","blob":"<66 hex>"}`.

- [ ] **Step 1: Forward frames instead of decoding them**

Replace the receive handler's `TRUSTED_NODES[]` lookup, zone mapping and per-metric UART
emission (`bridge_esp32.ino:148-183`) with a verbatim forward. The bridge no longer knows what
a zone is; the Pi maps MAC→zone from its trust store.

```cpp
  if (len != MESH_PACKET_LEN) { Serial.printf("[esp-now] bad size %d\n", len); return; }
  if (data[0] != MESH_MAGIC_V2) return;
  if (!meshVerifyNettag(data, bridgeNetKey)) return;   // cheap garbage drop
  if (meshDedupSeen(data + 1, (uint16_t)(data[7] | (data[8] << 8)))) return;

  char hex[MESH_PACKET_LEN * 2 + 1];
  for (int i = 0; i < MESH_PACKET_LEN; i++) sprintf(hex + i * 2, "%02x", data[i]);
  uartPrintf("{\"type\":\"frame\",\"data\":\"%s\"}", hex);
```

Handle a join beacon in the same callback:

```cpp
  if (len == (int)sizeof(MeshJoinBeacon) && data[1] == MESH_JOIN_MARKER) {
    char mac[13]; meshFormatMac(data + 2, mac);
    uartPrintf("{\"type\":\"unenrolled\",\"mac\":\"%s\"}", mac);
    return;
  }
```

- [ ] **Step 2: Read commands from the Pi**

The UART is one-way today — `serial_bridge.py` only calls `ser.readline()` and this sketch
parses nothing inbound. Add a line reader to `loop()`:

```cpp
// NetKey lives in RAM only, never NVS: a bridge stolen while powered off yields
// nothing, and the Pi re-sends on every serial connect so rotation is free.
static uint8_t bridgeNetKey[16];
static bool    bridgeHasNetKey = false;
static char    uartLine[256];
static int     uartLineLen = 0;

static bool hexToBytes(const char* hex, uint8_t* out, int outLen) {
  for (int i = 0; i < outLen; i++) {
    unsigned v;
    if (sscanf(hex + i * 2, "%2x", &v) != 1) return false;
    out[i] = (uint8_t)v;
  }
  return true;
}

static void handleUartLine(const char* line) {
  const char* key = strstr(line, "\"netkey\"");
  if (key) {
    const char* k = strstr(line, "\"key\":\"");
    if (k && hexToBytes(k + 7, bridgeNetKey, 16)) {
      bridgeHasNetKey = true;
      Serial.println("[bridge] netkey installed (RAM only)");
    }
    return;
  }
  if (strstr(line, "\"provision\"")) {
    const char* m = strstr(line, "\"mac\":\"");
    const char* b = strstr(line, "\"blob\":\"");
    uint8_t mac[6], blob[MESH_PROVISION_LEN];
    if (!m || !b || !hexToBytes(m + 7, mac, 6) ||
        !hexToBytes(b + 8, blob, MESH_PROVISION_LEN)) return;
    // Transient peer: added only to transmit, removed immediately. This is the
    // only time the bridge registers anything but broadcast.
    esp_now_peer_info_t p = {};
    memcpy(p.peer_addr, mac, 6);
    p.channel = MESH_FIXED_CHANNEL;
    p.encrypt = false;
    esp_now_add_peer(&p);
    esp_now_send(mac, blob, MESH_PROVISION_LEN);
    esp_now_del_peer(mac);
    Serial.println("[bridge] provision blob transmitted");
  }
}

static void pumpUart() {
  while (Serial1.available()) {
    char c = (char)Serial1.read();
    if (c == '\n') { uartLine[uartLineLen] = 0; handleUartLine(uartLine); uartLineLen = 0; }
    else if (uartLineLen < (int)sizeof(uartLine) - 1) uartLine[uartLineLen++] = c;
    else uartLineLen = 0;   // overlong line, discard rather than truncate-and-parse
  }
}
```

Call `pumpUart()` at the top of `loop()`, and skip beaconing entirely until
`bridgeHasNetKey` is true (an unsigned rank-0 beacon would be rejected by every node anyway).

- [ ] **Step 3: Commit**

```bash
git add firmware/bridge_esp32/bridge_esp32.ino
git commit -m "refactor: bridge forwards sealed frames and holds no keys in flash"
```

---

### Task 11: Pi decrypt path and provisioning relay

**Files:**
- Modify: `pi/scripts/serial_bridge.py`
- Test: `pi/tests/test_serial_bridge_mesh.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `handle_frame(client, msg, state) -> None`, `handle_unenrolled(msg, state) -> None`, `send_netkey(ser, net_key) -> None`, `send_provision(ser, mac, blob) -> None`, `accept_replay(state, mac, boot_count, seq) -> bool`, `unenrolled_macs(state) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pi/tests/test_serial_bridge_mesh.py -v`
Expected: FAIL — `AttributeError: module 'serial_bridge' has no attribute 'handle_frame'`

- [ ] **Step 3: Write minimal implementation**

Add to `pi/scripts/serial_bridge.py` (and register `frame` and `unenrolled` in the existing
`handle_line` dispatch alongside `reading`/`status`/`mesh`/`heartbeat`). Extend `new_state()`
with `'nodes_path': nodes.NODES_PATH`, `'net_key': None`, `'seen': {}`, `'unenrolled': {}`.

```python
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import mesh_crypto
import mesh_packet
import nodes as node_store

_METRICS = ('temperature', 'humidity', 'soil_moisture')


def accept_replay(state, mac: str, boot_count: int, seq: int) -> bool:
    """Reject replays without a clock: no node in this system has one.

    A higher boot_count always wins and resets the window, because a cold boot
    legitimately restarts seq at 0. A lower one is a rollback attempt.
    """
    last_boot, seen = state['seen'].get(mac, (-1, set()))
    if boot_count < last_boot:
        return False
    if boot_count > last_boot:
        state['seen'][mac] = (boot_count, {seq})
        return True
    if seq in seen:
        return False
    seen.add(seq)
    return True


def handle_frame(client, msg: dict, state: dict) -> None:
    try:
        raw = bytes.fromhex(msg['data'])
        header = mesh_packet.parse_header(raw)
    except (KeyError, ValueError) as exc:
        print(f'[mesh] malformed frame dropped: {exc}', flush=True)
        return

    mac = header.origin_mac.hex().upper()
    node = node_store.load(state['nodes_path']).get(mac)
    if node is None:
        state['unenrolled'][mac] = time.time()
        print(f'[mesh] frame from unenrolled {mac} — ignored', flush=True)
        return

    if not accept_replay(state, mac, header.boot_count, header.seq):
        print(f'[mesh] replay from {mac} dropped', flush=True)
        return

    try:
        body = mesh_crypto.open_packet(raw, node.app_key)
    except (mesh_crypto.MeshAuthError, mesh_crypto.MeshFormatError) as exc:
        # Distinct from a missing sensor: the packet arrived and was rejected.
        print(f'[mesh] {mac} failed authentication: {exc}', flush=True)
        return

    for metric in _METRICS:
        client.publish(_reading_topic(node.zone, 'sensors', metric),
                       f'{float(getattr(body, metric)):.1f}', retain=True)
    if body.battery_mv:
        client.publish(_battery_topic(mac), f'{body.battery_mv / 1000:.2f}', retain=True)
    client.publish(_status_topic(mac), 'online', retain=True)


def handle_unenrolled(msg: dict, state: dict) -> None:
    state['unenrolled'][node_store.normalise_mac(msg['mac'])] = time.time()


def unenrolled_macs(state: dict, max_age_s: float = 300.0) -> list:
    now = time.time()
    return [m for m, seen in state['unenrolled'].items() if now - seen <= max_age_s]


def _send_command(ser, payload: dict) -> None:
    ser.write((json.dumps(payload, separators=(',', ':')) + '\n').encode())


def send_netkey(ser, net_key: bytes) -> None:
    _send_command(ser, {'type': 'netkey', 'key': net_key.hex()})


def send_provision(ser, mac: str, blob: bytes) -> None:
    _send_command(ser, {'type': 'provision', 'mac': node_store.normalise_mac(mac),
                        'blob': blob.hex()})
```

In `run()`, call `send_netkey(ser, state['net_key'])` immediately after the serial port opens,
so the bridge is re-keyed on every reconnect.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest pi/tests/test_serial_bridge_mesh.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/serial_bridge.py pi/tests/test_serial_bridge_mesh.py
git commit -m "feat: Pi decrypts sealed frames, rejects replays, relays provisioning"
```

---

### Task 12: The /api/nodes endpoints

**Files:**
- Modify: `pi/portal/portal.py`
- Test: `pi/tests/test_portal_nodes.py`

**Interfaces:**
- Consumes: Tasks 2, 3, 11.
- Produces: `GET /api/nodes` → `{"nodes":[{"mac","zone","name","sleepy"}],"unenrolled":[mac]}`; `POST /api/nodes` accepting `{"mac","key","zone","name","sleepy"}` → 201; `DELETE /api/nodes/<mac>` → 204. All bearer-token gated by the same decorator `/api/history` uses.

- [ ] **Step 1: Write the failing test**

```python
# pi/tests/test_portal_nodes.py
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'portal'))

import nodes
import portal

KEY = bytes(range(16)).hex()
MAC = '206EF16C9DB0'


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = str(tmp_path / 'nodes.json')
    monkeypatch.setattr(portal, 'NODES_PATH', store, raising=False)
    portal.app.config['TESTING'] = True
    with portal.app.test_client() as c:
        c._store = store
        yield c


def _auth():
    return {'Authorization': f'Bearer {portal._api_token()}'}


def test_post_requires_a_token(client):
    r = client.post('/api/nodes', json={'mac': MAC, 'key': KEY,
                                        'zone': 'zone2', 'name': 'x', 'sleepy': True})
    assert r.status_code == 401


def test_post_enrols_a_sensor(client):
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': MAC, 'key': KEY, 'zone': 'zone2',
                          'name': 'Tomato bed', 'sleepy': True})
    assert r.status_code == 201
    assert MAC in nodes.load(client._store)


def test_post_rejects_a_key_that_is_not_16_bytes(client):
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': MAC, 'key': 'aabb', 'zone': 'z',
                          'name': 'x', 'sleepy': False})
    assert r.status_code == 400


def test_post_rejects_a_malformed_mac(client):
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': 'nope', 'key': KEY, 'zone': 'z',
                          'name': 'x', 'sleepy': False})
    assert r.status_code == 400


def test_get_never_leaks_app_keys(client):
    client.post('/api/nodes', headers=_auth(),
                json={'mac': MAC, 'key': KEY, 'zone': 'zone2',
                      'name': 'Tomato bed', 'sleepy': True})
    r = client.get('/api/nodes', headers=_auth())
    assert r.status_code == 200
    assert KEY not in r.get_data(as_text=True)
    assert 'key' not in r.get_json()['nodes'][0]
    assert r.get_json()['nodes'][0]['zone'] == 'zone2'


def test_delete_removes_and_is_idempotent(client):
    client.post('/api/nodes', headers=_auth(),
                json={'mac': MAC, 'key': KEY, 'zone': 'z', 'name': 'x',
                      'sleepy': False})
    assert client.delete(f'/api/nodes/{MAC}', headers=_auth()).status_code == 204
    assert client.delete(f'/api/nodes/{MAC}', headers=_auth()).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pi/tests/test_portal_nodes.py -v`
Expected: FAIL — 404 on every route

- [ ] **Step 3: Write minimal implementation**

```python
# in pi/portal/portal.py, beside the existing /api/history routes
from nodes import NODES_PATH, Node, NodeStoreError, load, normalise_mac, remove
from nodes import add as add_node

APP_KEY_HEX_LEN = 32


@app.route('/api/nodes', methods=['GET'])
@require_api_token
def api_nodes_list():
    store = load(NODES_PATH)
    return jsonify({
        # AppKeys are deliberately absent: this endpoint is reachable from the
        # LAN and a key here would undo the whole per-device key model.
        'nodes': [{'mac': n.mac, 'zone': n.zone, 'name': n.name, 'sleepy': n.sleepy}
                  for n in store.values()],
        'unenrolled': read_unenrolled(),
    })


@app.route('/api/nodes', methods=['POST'])
@require_api_token
def api_nodes_add():
    body = request.get_json(silent=True) or {}
    try:
        mac = normalise_mac(body.get('mac', ''))
        key = bytes.fromhex(body.get('key', ''))
    except ValueError:
        return jsonify({'error': 'bad mac or key'}), 400
    if len(key) != 16:
        return jsonify({'error': 'key must be 16 bytes'}), 400
    zone = (body.get('zone') or '').strip()
    if not zone:
        return jsonify({'error': 'zone is required'}), 400

    node = Node(mac, key, zone, (body.get('name') or zone).strip(),
                bool(body.get('sleepy', True)))
    try:
        add_node(node, NODES_PATH)
    except NodeStoreError as exc:
        return jsonify({'error': str(exc)}), 500
    queue_provision(node)      # serial_bridge picks this up and seals the NetKey
    return jsonify({'mac': mac}), 201


@app.route('/api/nodes/<mac>', methods=['DELETE'])
@require_api_token
def api_nodes_delete(mac):
    try:
        removed = remove(mac, NODES_PATH)
    except ValueError:
        return jsonify({'error': 'bad mac'}), 400
    return ('', 204) if removed else (jsonify({'error': 'unknown mac'}), 404)
```

`queue_provision` and `read_unenrolled` cross the process boundary between `portal.py` and
`serial_bridge.py` using the retain+poll pattern this codebase already proved for rule sync:
`queue_provision` publishes the sealed blob to the retained MQTT topic
`greenhouse/provision/<mac>`, and `serial_bridge.py` subscribes to `greenhouse/provision/+`,
calls `send_provision()`, then clears the topic with an empty retained publish.
`read_unenrolled` reads the retained `greenhouse/unenrolled` topic that `serial_bridge.py`
publishes from `unenrolled_macs()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest pi/tests/test_portal_nodes.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add pi/portal/portal.py pi/tests/test_portal_nodes.py
git commit -m "feat: /api/nodes enrolment endpoints, keys never served"
```

---

### Task 13: Provisioning tool and installer changes

**Files:**
- Create: `pi/tools/provision_sensor.py`
- Modify: `pi/install.sh:21-26` (apt list) and the device.json backfill section

**Interfaces:**
- Consumes: Task 3.
- Produces: a QR PNG plus a printable card; QR payload is exactly `{"v":1,"mac":"<12 hex>","k":"<32 hex>"}` — Task 14's parser depends on these key names.

- [ ] **Step 1: Add the apt dependency and NetKey generation**

In `pi/install.sh`, add `python3-cryptography \` to the `apt-get install` list (after
`python3-paho-mqtt`), with a comment matching the file's style:

```bash
  # python3-cryptography: mesh packet AES-GCM/CMAC. Installed via apt, NOT pip —
  # this board is ARMv6 and pip would try to build it from source through Rust.
  python3-cryptography \
```

Alongside the existing `device.json` field backfill, generate the NetKey once if absent
(same "only backfill what's missing, never regenerate" rule the file already follows, since
regenerating would orphan every enrolled sensor):

```bash
if [ ! -f /etc/greenhouse/netkey ]; then
  openssl rand -hex 16 > /etc/greenhouse/netkey
  echo "[install] generated a new mesh NetKey"
fi
chown pi:pi /etc/greenhouse/netkey
chmod 600 /etc/greenhouse/netkey
touch /etc/greenhouse/nodes.json
chown pi:pi /etc/greenhouse/nodes.json
chmod 600 /etc/greenhouse/nodes.json
```

- [ ] **Step 2: Write the provisioning tool**

```python
#!/usr/bin/env python3
"""Generate a sensor's AppKey, the header to flash it with, and its QR label.

Run once per physical sensor before flashing it. The QR is the ONLY copy of the
key besides the sensor itself and the Pi, so print it and stick it on the box.
"""
import argparse
import json
import os
import secrets

import qrcode

HEADER_TEMPLATE = '''// Generated by provision_sensor.py — one sensor, one key. Do not commit.
#pragma once
#include <stdint.h>
static const uint8_t NODE_APP_KEY[16] = { %s };
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mac', required=True, help='sensor MAC, any separator style')
    ap.add_argument('--out', default='.', help='directory for the QR and header')
    args = ap.parse_args()

    mac = ''.join(c for c in args.mac if c.isalnum()).upper()
    if len(mac) != 12:
        raise SystemExit(f'not a MAC address: {args.mac}')

    key = secrets.token_bytes(16)
    payload = json.dumps({'v': 1, 'mac': mac, 'k': key.hex()}, separators=(',', ':'))

    os.makedirs(args.out, exist_ok=True)
    qr_path = os.path.join(args.out, f'sensor-{mac}.png')
    qrcode.make(payload).save(qr_path)

    header_path = os.path.join(args.out, f'node_key-{mac}.h')
    body = ', '.join(f'0x{b:02X}' for b in key)
    with open(header_path, 'w') as fh:
        fh.write(HEADER_TEMPLATE % body)
    os.chmod(header_path, 0o600)

    print(f'MAC     : {mac}')
    print(f'QR      : {qr_path}   ← print and stick on the sensor')
    print(f'Header  : {header_path}   ← copy next to the sketch, then flash')
    print('This key exists nowhere else. Losing both the QR and the sensor means '
          'the sensor can never be re-enrolled.')


if __name__ == '__main__':
    main()
```

- [ ] **Step 3: Verify it runs and commit**

Run: `python pi/tools/provision_sensor.py --mac 20:6E:F1:6C:9D:B0 --out /tmp/prov`
Expected: prints three lines; `/tmp/prov` contains a PNG and a `.h`

```bash
git add pi/tools/provision_sensor.py pi/install.sh
git commit -m "feat: per-sensor key provisioning tool and NetKey generation on install"
```

---

### Task 14: App — QR payload model and provisioning service

**Files:**
- Create: `app/lib/models/sensor_enrolment.dart`
- Create: `app/lib/services/sensor_provisioning_service.dart`
- Test: `app/test/models/sensor_enrolment_test.dart`, `app/test/services/sensor_provisioning_service_test.dart`

**Interfaces:**
- Consumes: Task 12's endpoints, Task 13's QR payload shape.
- Produces: `SensorEnrolment` (`mac`, `key`) with `SensorEnrolment.fromQr(String) -> SensorEnrolment` throwing `FormatException`; `SensorProvisioningService` with `Future<void> enrol({required SensorEnrolment sensor, required String zone, required String name, required bool sleepy})`, `Future<List<String>> unenrolled()`, `Future<void> remove(String mac)`.

- [ ] **Step 1: Write the failing test**

```dart
// app/test/models/sensor_enrolment_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/sensor_enrolment.dart';

void main() {
  const validKey = '000102030405060708090a0b0c0d0e0f';

  test('parses the QR payload the provisioning tool writes', () {
    final s = SensorEnrolment.fromQr(
        '{"v":1,"mac":"206EF16C9DB0","k":"$validKey"}');
    expect(s.mac, '206EF16C9DB0');
    expect(s.key, validKey);
  });

  test('uppercases and strips separators from the MAC', () {
    final s = SensorEnrolment.fromQr(
        '{"v":1,"mac":"20:6e:f1:6c:9d:b0","k":"$validKey"}');
    expect(s.mac, '206EF16C9DB0');
  });

  test('rejects a pairing QR scanned by mistake', () {
    expect(() => SensorEnrolment.fromQr('{"host":"greenhouse.local","port":8883}'),
        throwsFormatException);
  });

  test('rejects a key that is not 16 bytes', () {
    expect(() => SensorEnrolment.fromQr('{"v":1,"mac":"206EF16C9DB0","k":"aabb"}'),
        throwsFormatException);
  });

  test('rejects a future payload version rather than guessing', () {
    expect(() => SensorEnrolment.fromQr(
        '{"v":2,"mac":"206EF16C9DB0","k":"$validKey"}'), throwsFormatException);
  });

  test('rejects text that is not JSON at all', () {
    expect(() => SensorEnrolment.fromQr('hello'), throwsFormatException);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && flutter test test/models/sensor_enrolment_test.dart`
Expected: FAIL — `Target of URI doesn't exist: 'package:greenhouse_app/models/sensor_enrolment.dart'`

- [ ] **Step 3: Write minimal implementation**

```dart
// app/lib/models/sensor_enrolment.dart
import 'dart:convert';

/// A sensor's identity as printed on its box, scanned from the QR that
/// `pi/tools/provision_sensor.py` generates. The key travels no other way.
class SensorEnrolment {
  const SensorEnrolment({required this.mac, required this.key});

  final String mac; // 12 uppercase hex, no separators
  final String key; // 32 hex chars = 16 bytes

  static final _macPattern = RegExp(r'^[0-9A-F]{12}$');
  static final _keyPattern = RegExp(r'^[0-9a-fA-F]{32}$');

  factory SensorEnrolment.fromQr(String raw) {
    final Object? decoded;
    try {
      decoded = jsonDecode(raw);
    } on FormatException {
      throw const FormatException('That is not a sensor code.');
    }
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('That is not a sensor code.');
    }
    if (decoded['v'] != 1) {
      throw const FormatException(
          'This sensor needs a newer app version to add.');
    }
    final mac = (decoded['mac'] as String? ?? '')
        .replaceAll(RegExp(r'[^0-9A-Fa-f]'), '')
        .toUpperCase();
    final key = decoded['k'] as String? ?? '';
    if (!_macPattern.hasMatch(mac) || !_keyPattern.hasMatch(key)) {
      throw const FormatException('That sensor code is damaged or incomplete.');
    }
    return SensorEnrolment(mac: mac, key: key);
  }
}
```

```dart
// app/lib/services/sensor_provisioning_service.dart
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/connection_config.dart';
import '../models/sensor_enrolment.dart';

class SensorProvisioningService {
  SensorProvisioningService({required this.config, http.Client? client})
      : _client = client ?? http.Client();

  final ConnectionConfig config;
  final http.Client _client;

  Uri _url(String path) =>
      Uri.parse('https://${config.host}:${config.port}$path');

  Map<String, String> get _headers => {
        'Authorization': 'Bearer ${config.apiToken}',
        'Content-Type': 'application/json',
      };

  Future<void> enrol({
    required SensorEnrolment sensor,
    required String zone,
    required String name,
    required bool sleepy,
  }) async {
    final res = await _client.post(_url('/api/nodes'),
        headers: _headers,
        body: jsonEncode({
          'mac': sensor.mac,
          'key': sensor.key,
          'zone': zone,
          'name': name,
          'sleepy': sleepy,
        }));
    if (res.statusCode != 201) {
      throw http.ClientException('Enrolment failed (${res.statusCode})');
    }
  }

  Future<List<String>> unenrolled() async {
    final res = await _client.get(_url('/api/nodes'), headers: _headers);
    if (res.statusCode != 200) return const [];
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    return (body['unenrolled'] as List? ?? const []).cast<String>();
  }

  Future<void> remove(String mac) async {
    final res = await _client.delete(_url('/api/nodes/$mac'), headers: _headers);
    if (res.statusCode != 204) {
      throw http.ClientException('Removal failed (${res.statusCode})');
    }
  }
}
```

- [ ] **Step 4: Write the service test**

```dart
// app/test/services/sensor_provisioning_service_test.dart
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/sensor_enrolment.dart';
import 'package:greenhouse_app/services/sensor_provisioning_service.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import '../helpers/test_config.dart'; // existing helper building a ConnectionConfig

void main() {
  const sensor = SensorEnrolment(
      mac: '206EF16C9DB0', key: '000102030405060708090a0b0c0d0e0f');

  test('enrol posts mac, key, zone and role', () async {
    late http.Request seen;
    final svc = SensorProvisioningService(
      config: testConfig(apiToken: 'tok'),
      client: MockClient((req) async {
        seen = req;
        return http.Response('{"mac":"206EF16C9DB0"}', 201);
      }),
    );
    await svc.enrol(sensor: sensor, zone: 'zone5', name: 'Basil', sleepy: true);
    final body = jsonDecode(seen.body) as Map<String, dynamic>;
    expect(body['mac'], '206EF16C9DB0');
    expect(body['zone'], 'zone5');
    expect(body['sleepy'], true);
    expect(seen.headers['Authorization'], 'Bearer tok');
  });

  test('enrol throws when the Pi rejects the request', () async {
    final svc = SensorProvisioningService(
      config: testConfig(apiToken: 'tok'),
      client: MockClient((_) async => http.Response('{"error":"bad"}', 400)),
    );
    expect(
        () => svc.enrol(
            sensor: sensor, zone: 'z', name: 'n', sleepy: false),
        throwsA(isA<http.ClientException>()));
  });

  test('unenrolled returns an empty list rather than throwing on error',
      () async {
    final svc = SensorProvisioningService(
      config: testConfig(apiToken: 'tok'),
      client: MockClient((_) async => http.Response('nope', 500)),
    );
    expect(await svc.unenrolled(), isEmpty);
  });
}
```

- [ ] **Step 5: Run tests and commit**

Run: `cd app && flutter test test/models/sensor_enrolment_test.dart test/services/sensor_provisioning_service_test.dart && flutter analyze`
Expected: PASS (9 tests), analyze clean

```bash
git add app/lib/models/sensor_enrolment.dart \
        app/lib/services/sensor_provisioning_service.dart \
        app/test/models/sensor_enrolment_test.dart \
        app/test/services/sensor_provisioning_service_test.dart
git commit -m "feat: sensor QR payload model and enrolment service"
```

---

### Task 15: App — the Add sensor flow

**Files:**
- Create: `app/lib/screens/devices/add_sensor_screen.dart`
- Modify: `app/lib/screens/devices/devices_screen.dart`
- Test: `app/test/widgets/add_sensor_screen_test.dart`

**Interfaces:**
- Consumes: Task 14, and the existing `screens/pairing/qr_scan_screen.dart`.
- Produces: `AddSensorScreen` (route `/devices/add`).

- [ ] **Step 1: Write the failing test**

```dart
// app/test/widgets/add_sensor_screen_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/sensor_enrolment.dart';
import 'package:greenhouse_app/screens/devices/add_sensor_screen.dart';

void main() {
  const sensor = SensorEnrolment(
      mac: '206EF16C9DB0', key: '000102030405060708090a0b0c0d0e0f');

  Widget host({required Future<void> Function(String, String, bool) onSubmit}) =>
      MaterialApp(
          home: AddSensorScreen(scanned: sensor, onSubmit: onSubmit));

  testWidgets('tells the owner to hold the sensor near the hub', (t) async {
    await t.pumpWidget(host(onSubmit: (_, __, ___) async {}));
    expect(find.textContaining('near the hub'), findsOneWidget);
  });

  testWidgets('will not submit without a zone name', (t) async {
    var submitted = false;
    await t.pumpWidget(host(onSubmit: (_, __, ___) async { submitted = true; }));
    await t.tap(find.text('Add sensor'));
    await t.pump();
    expect(submitted, isFalse);
    expect(find.textContaining('Give this sensor a place'), findsOneWidget);
  });

  testWidgets('submits the zone, name and battery choice', (t) async {
    String? zone;
    bool? sleepy;
    await t.pumpWidget(host(onSubmit: (z, _, s) async { zone = z; sleepy = s; }));
    await t.enterText(find.byKey(const Key('zoneField')), 'Basil bed');
    await t.tap(find.byKey(const Key('batteryToggle')));
    await t.pump();
    await t.tap(find.text('Add sensor'));
    await t.pumpAndSettle();
    expect(zone, 'Basil bed');
    expect(sleepy, isFalse);
  });

  testWidgets('shows a plain-language error when enrolment fails', (t) async {
    await t.pumpWidget(host(onSubmit: (_, __, ___) async {
      throw Exception('boom');
    }));
    await t.enterText(find.byKey(const Key('zoneField')), 'Basil bed');
    await t.tap(find.text('Add sensor'));
    await t.pumpAndSettle();
    expect(find.textContaining("couldn't add"), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && flutter test test/widgets/add_sensor_screen_test.dart`
Expected: FAIL — `Target of URI doesn't exist: '.../add_sensor_screen.dart'`

- [ ] **Step 3: Write minimal implementation**

```dart
// app/lib/screens/devices/add_sensor_screen.dart
import 'package:flutter/material.dart';

import '../../models/sensor_enrolment.dart';

/// Step two of adding a sensor: the QR is already scanned, this collects where
/// the sensor lives and how it is powered, then hands off to the Pi.
class AddSensorScreen extends StatefulWidget {
  const AddSensorScreen({
    super.key,
    required this.scanned,
    required this.onSubmit,
  });

  final SensorEnrolment scanned;

  /// (zone, name, sleepy) — sleepy means battery powered.
  final Future<void> Function(String zone, String name, bool sleepy) onSubmit;

  @override
  State<AddSensorScreen> createState() => _AddSensorScreenState();
}

class _AddSensorScreenState extends State<AddSensorScreen> {
  final _zone = TextEditingController();
  bool _battery = true;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _zone.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final zone = _zone.text.trim();
    if (zone.isEmpty) {
      setState(() => _error = 'Give this sensor a place, like "Tomato bed".');
      return;
    }
    setState(() { _busy = true; _error = null; });
    try {
      await widget.onSubmit(zone, zone, _battery);
      if (mounted) Navigator.of(context).pop(true);
    } catch (_) {
      if (mounted) {
        setState(() => _error =
            "We couldn't add this sensor. Keep it near the hub and try again.");
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Add sensor')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Card(
            child: Padding(
              padding: EdgeInsets.all(12),
              child: Text(
                'Keep the sensor switched on and near the hub while you add it. '
                'Once it has joined you can put it wherever you like.',
              ),
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            key: const Key('zoneField'),
            controller: _zone,
            decoration: const InputDecoration(
              labelText: 'Where is this sensor?',
              hintText: 'Tomato bed',
            ),
          ),
          SwitchListTile(
            key: const Key('batteryToggle'),
            value: _battery,
            onChanged: (v) => setState(() => _battery = v),
            title: const Text('Runs on batteries'),
            subtitle: const Text(
                'Battery sensors sleep between readings to last longer. '
                'Turn this off if it is plugged in.'),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _busy ? null : _submit,
            child: _busy
                ? const SizedBox(
                    height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('Add sensor'),
          ),
        ],
      ),
    );
  }
}
```

- [ ] **Step 4: Wire it into the Devices screen**

Add an `Add sensor` FAB to `devices_screen.dart` that opens the existing
`QrScanScreen`, parses the result with `SensorEnrolment.fromQr`, and pushes
`AddSensorScreen` with an `onSubmit` that calls `SensorProvisioningService.enrol`. Show any
`FormatException` message from the parser directly — they are already written as
plain-language sentences. Surface `unenrolled()` results as a dismissible banner reading
"A sensor nearby hasn't been added yet — scan the code on its box."

- [ ] **Step 5: Run tests and commit**

Run: `cd app && flutter test && flutter analyze`
Expected: PASS, analyze clean

```bash
git add app/lib/screens/devices/add_sensor_screen.dart \
        app/lib/screens/devices/devices_screen.dart \
        app/test/widgets/add_sensor_screen_test.dart
git commit -m "feat: add-sensor flow from QR scan to enrolment"
```

---

### Task 16: Flag day — flash, enrol, verify, document

The only task requiring hardware. Nothing before it touches a deployed device.

**Files:**
- Modify: `firmware/test/vectors/device_vectors.txt`, `docs/DEVICES.md`, `docs/SCALING_AND_EXPANSION_IDEAS.md`, `docs/technical/02-esp-now-protocol.md`, `docs/technical/03-mesh-routing.md`, `docs/technical/10-security.md`, `SECURITY.md`, `HANDOFF.md`

- [ ] **Step 1: Capture the device crypto vectors**

Flash `firmware/crypto_selftest` to any C3, copy the `packet=` line it prints into
`firmware/test/vectors/device_vectors.txt`, then run:

Run: `python -m pytest pi/tests/test_firmware_vectors.py -v`
Expected: PASS (3 tests) — this is the moment mbedTLS and `cryptography` are proven to agree.
A failure here means stop and fix the format before flashing anything else.

- [ ] **Step 2: Deploy the Pi first**

Run: `./deploy.ps1` then on the Pi `sudo bash pi/scripts/selftest.sh`
Expected: selftest passes; `/etc/greenhouse/netkey` and `nodes.json` exist, both `0600 pi:pi`

- [ ] **Step 3: Flash the bridge, then each sensor**

For each sensor, generate its key first, keep the printed QR with the physical board, then
flash:

```bash
python pi/tools/provision_sensor.py --mac <MAC> --out ./provisioning
```

- [ ] **Step 4: Enrol every sensor from the phone**

For each: open the app → Devices → Add sensor → scan its QR → name the zone → confirm. Expect
the sensor to appear on the dashboard within one reading interval. **This is the acceptance
test for the whole plan: it must work with no cable attached to anything.**

- [ ] **Step 5: Prove the limit is actually gone**

Enrol a fifth and sixth sensor (re-flashing any spare C3 with its own key). Confirm both
report. Confirm on the mesh map that a relayed node still shows rank 2.

- [ ] **Step 6: Update the docs**

`SCALING_AND_EXPANSION_IDEAS.md` gets a header pointing at the spec and noting §1's limits no
longer apply and §7's "do not implement" recommendation was superseded. `SECURITY.md`'s
shared-key gap is closed — replace it with the residual risks from the spec's §Security
analysis. `docs/DEVICES.md` stops listing `TRUSTED_NODES[]` and describes the app flow.
`HANDOFF.md` gets this session's TL;DR.

- [ ] **Step 7: Final verification and commit**

Run: `python -m pytest pi/tests/ -v && cd app && flutter test && flutter analyze`
Expected: all green

```bash
git add firmware/test/vectors/device_vectors.txt docs/ SECURITY.md HANDOFF.md
git commit -m "docs: record the unlimited-sensor migration and close the shared-key gap"
```

---

## Self-Review

**Spec coverage.** Key model → Tasks 3, 5, 13. Wire formats → 1, 4, 8. Nonce construction →
1, 5, 11. Peer-limit removal → 7. Provisioning flow → 9, 10, 11, 12, 14, 15. Component
changes → 7-15. Security analysis → 3 (0600), 12 (keys never served), 8 (nettag), 11
(replay). Testing strategy → 4 (host harness), 6 (device vectors), 11-15 (unit), 16 (bench).
Migration → 16. Failure modes → 3 (corrupt store), 9 (blob never arrives), 3 (double enrol),
11 (replay window). No spec section is unimplemented.

**Open questions from the spec** are resolved here as follows: AES-GCM is chosen (Task 6) with
the ChaCha20 comparison left as a post-deployment measurement; the GCM tag stays a full 16
bytes, since 61 bytes is far inside the 250-byte limit and truncation buys nothing worth the
forgery-resistance cost; the AppKey is compiled in via a generated header (Task 13) rather
than flashed to a separate NVS partition, because that needs no extra `esptool` step and the
tool already writes the header.

**Type consistency.** `SensorBody`/`MeshHeader` field names are identical across Tasks 1, 2
and 11. `mesh_packet.h`'s constants match `mesh_packet.py`'s and are asserted equal in Task
4. The QR key names `v`/`mac`/`k` are written in Task 13 and parsed in Task 14. `sleepy`
carries the same meaning from the app form through `/api/nodes`, `nodes.Node`, the provision
blob and `meshStoreSetSleepy`. `MESH_PROVISION_LEN` (33) is used identically in Tasks 6, 9
and 10.
