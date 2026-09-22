# LoRaWAN Multi-Greenhouse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remote greenhouses with no internet report compact sensor summaries and alerts over LoRaWAN to one shared gateway, which republishes them (and pushes alerts) through its own LTE uplink, and relays actuator commands back down — with no SIM card or subscription at any remote site.

**Architecture:** Remote site = today's Pi Zero W stack + a RAK3172 Evaluation Board on USB (`/dev/lora`), driven by a new `lora_uplink.py` service that averages local MQTT readings and sends one compact uplink per report interval (Class C, so commands arrive any time). Gateway = Raspberry Pi 4 running the greenhouse stack of its own site plus a RAK2287 concentrator HAT, ChirpStack (SQLite build) + Concentratord + MQTT forwarder + Redis, a SIM7600G-H in RNDIS mode on a powered USB hub, and a new `lora_gateway_bridge.py` that maps ChirpStack events to `greenhouse/sites/<site_id>/…` topics, which the existing HiveMQ bridge already forwards (`greenhouse/#`).

**Tech Stack:** Python 3 (paho-mqtt, pyserial — both already used), pytest; RAK3172 RUI3 AT firmware; ChirpStack v4 (≥ 4.10.1 for SQLite), chirpstack-concentratord (SX1302), chirpstack-mqtt-forwarder, Redis ≥ 6.2, Mosquitto 2.x; Raspberry Pi OS 64-bit (gateway), existing Raspberry Pi OS (remote Zero W).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-site-lorawan-cellular-design.md`, **including its "Revision 2026-09-23" section** (hardware corrections: USB not UART on the Zero W, Pi 4 gateway, UART3 remap, USB modem). Report §21.7 has the link-budget derivation.

## Global Constraints

- EU868 only: `AT+BAND=4`. End-device TX 14 dBm, ADR on. Uplink payload must fit **51 bytes** (DR0/SF12 cap) — every encoder enforces it.
- Remote-site uplink cadence: one summary per `report_interval_s` (default **600 s**), alerts rate-limited to **1 per rule per 900 s**. At SF12 (≈1.5 s airtime) that is ≤ ~12 s/hour of airtime, inside the 36 s/hour 1 % rule.
- Remote data lives only under `greenhouse/sites/<site_id>/…` (never `greenhouse/<zone>/…`), so the gateway site's own dashboard and recorder are untouched.
- Do **not** stack two HATs on the gateway Pi: RAK2287 Pi HAT on the header, SIM7600G-H over USB only.
- Pi code style: same as `pi/scripts/serial_bridge.py` (module-level pure helpers + a thin `run()`/`main()`), tests with fakes, no mocking frameworks.
- Run `python -m pytest pi/tests/ -v` after every software task (the Windows-only `test_nodes.py::test_store_is_written_0600…` failure is pre-existing).
- **Nothing in this plan is to be executed until the user says so.**

## File map

| File | Responsibility |
|---|---|
| `pi/shared/lora_payload.py` | Byte formats: summary (fPort 1), alert (fPort 2), command (fPort 10) |
| `pi/shared/rak3172.py` | RUI3 AT-command driver + event parser |
| `pi/scripts/lora_uplink.py` | Remote-site service: aggregate → uplink; downlink → local MQTT |
| `pi/scripts/lora_gateway_bridge.py` | Gateway service: ChirpStack ↔ `greenhouse/sites/…` |
| `pi/scripts/serial_bridge.py` | Serial port becomes configurable (UART3 on the gateway) |
| `pi/systemd/greenhouse-lora-uplink.service`, `pi/systemd/greenhouse-lora-gateway.service` | Services |
| `pi/udev/99-greenhouse-lora.rules` | Stable `/dev/lora` name for the RAK3172-E |
| `pi/config/lora.json.example`, `pi/config/lora_sites.json.example` | Per-site / gateway config templates |
| `docs/LORAWAN_SETUP.md` | Hardware + OS bring-up runbook (Tasks H1–H3) |

---

## Part S — Software (TDD)

### Task S1: Payload formats

**Files:** Create `pi/shared/lora_payload.py`; Test `pi/tests/test_lora_payload.py`.

**Interfaces (produced):**

```python
MAX_PAYLOAD = 51
PORT_SUMMARY, PORT_ALERT, PORT_COMMAND = 1, 2, 10
@dataclass class ZoneSummary: zone: int; temperature: float|None; humidity: float|None; soil: float|None
def zone_number(zone_name: str) -> int | None          # 'zone3' -> 3
def encode_summaries(zones: list[ZoneSummary]) -> list[bytes]   # chunked, each <= 51 B
def decode_summary(data: bytes) -> list[ZoneSummary]
def encode_alert(severity: str, rule_id: str) -> bytes
def decode_alert(data: bytes) -> dict                    # {'severity':..., 'rule_id':...}
def encode_command(actuator: str, on: bool) -> bytes
def decode_command(data: bytes) -> tuple[str, bool]
```

Summary v1: `[0x01][n]` then per zone 7 bytes `[zone u8][temp×10 i16 BE][rh×10 u16 BE][soil×10 u16 BE]`; missing value = `0x7FFF` (temp) / `0xFFFF` (rh, soil). 7 zones per message (2 + 7·7 = 51). Alert v1: `[0x01][severity 0 info|1 warning|2 critical][rule_id ASCII ≤ 40]`. Command v1: `[0x01][0 OFF|1 ON][actuator ASCII ≤ 40]`.

- [ ] **Step 1: Failing tests** — `pi/tests/test_lora_payload.py`:

```python
# pi/tests/test_lora_payload.py
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import lora_payload as lp


def test_zone_number_parses_the_project_convention():
    assert lp.zone_number('zone3') == 3
    assert lp.zone_number('Tomatoes') is None


def test_summary_round_trip_with_one_decimal_precision():
    zs = [lp.ZoneSummary(2, 24.46, 61.04, 42.0), lp.ZoneSummary(3, -3.2, None, 10.55)]
    (msg,) = lp.encode_summaries(zs)
    out = lp.decode_summary(msg)
    assert out == [lp.ZoneSummary(2, 24.5, 61.0, 42.0), lp.ZoneSummary(3, -3.2, None, 10.6)]


def test_summaries_are_chunked_to_the_sf12_cap():
    zs = [lp.ZoneSummary(i, 20.0, 50.0, 30.0) for i in range(1, 16)]
    msgs = lp.encode_summaries(zs)
    assert [len(m) for m in msgs] == [51, 51, 9]
    assert sum(len(lp.decode_summary(m)) for m in msgs) == 15


def test_alert_round_trip_and_length_cap():
    data = lp.encode_alert('warning', 'soil-dry-zone3')
    assert lp.decode_alert(data) == {'severity': 'warning', 'rule_id': 'soil-dry-zone3'}
    assert len(lp.encode_alert('critical', 'x' * 100)) <= lp.MAX_PAYLOAD


def test_command_round_trip():
    assert lp.decode_command(lp.encode_command('pump1', True)) == ('pump1', True)
    assert lp.decode_command(lp.encode_command('fan', False)) == ('fan', False)


def test_unknown_version_is_rejected():
    with pytest.raises(ValueError):
        lp.decode_summary(b'\x02\x00')
```

- [ ] **Step 2:** `python -m pytest pi/tests/test_lora_payload.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement** `pi/shared/lora_payload.py`:

```python
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
    return bytes([VERSION, 1 if on else 0]) + _text(actuator)


def decode_command(data: bytes):
    if len(data) < 3 or data[0] != VERSION or data[1] > 1:
        raise ValueError('unsupported command')
    return data[2:].decode('ascii', 'replace'), data[1] == 1
```

- [ ] **Step 4:** Test → PASS. **Step 5:** Commit `feat(lora): compact uplink/downlink payload formats (<= 51 bytes)`.

### Task S2: RAK3172 AT driver

**Files:** Create `pi/shared/rak3172.py`; Test `pi/tests/test_rak3172.py`.

**Interfaces (produced):**

```python
class RakError(RuntimeError)
def parse_event(line: str) -> dict | None
class Rak3172:
    def __init__(self, ser, clock=time.monotonic)
    def command(self, cmd: str, timeout: float = 3.0) -> list[str]
    def configure_otaa(self, dev_eui: str, app_eui: str, app_key: str) -> None
    def join(self, timeout: float = 120.0) -> bool
    def send(self, port: int, payload: bytes, timeout: float = 15.0) -> bool
    def poll_events(self) -> list[dict]
```

RUI3 facts used (RAK RUI3 AT Command Manual): `AT+NWM=1`, `AT+BAND=4` (EU868), `AT+NJM=1` (OTAA), `AT+CLASS=C`, `AT+ADR=1`, `AT+DEVEUI=`, `AT+APPEUI=`, `AT+APPKEY=`, `AT+JOIN=1:0:10:8`, `AT+SEND=<port>:<hex>`; events `+EVT:JOINED`, `+EVT:JOIN_FAILED_RX_TIMEOUT` / `_TX_TIMEOUT`, `+EVT:TX_DONE`, `+EVT:RX_1|RX_2|RX_B|RX_C:<rssi>:<snr>:UNICAST:<port>:<hex>`; errors are lines starting `AT_` (e.g. `AT_ERROR`, `AT_PARAM_ERROR`, `AT_BUSY_ERROR`, `AT_NO_NETWORK_JOINED`).

- [ ] **Step 1: Failing tests** — `pi/tests/test_rak3172.py`:

```python
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
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `pi/shared/rak3172.py`:

```python
"""RAK3172 (RUI3 firmware) AT-command driver.

The module runs the whole LoRaWAN stack itself (join, keys, duty cycle,
retransmissions); the Pi only issues AT commands over USB-serial. Async
+EVT lines can arrive interleaved with command replies -- they are queued,
never lost, and handed out by poll_events().
"""
import time

_TERMINAL_OK = 'OK'


class RakError(RuntimeError):
    pass


def parse_event(line: str):
    line = line.strip()
    if not line.startswith('+EVT:'):
        return None
    body = line[5:]
    if body == 'JOINED':
        return {'type': 'joined'}
    if body.startswith('JOIN_FAILED'):
        return {'type': 'join_failed'}
    if body == 'TX_DONE' or body == 'SEND_CONFIRMED_OK':
        return {'type': 'tx_done'}
    if body == 'SEND_CONFIRMED_FAILED':
        return {'type': 'tx_failed'}
    if body.startswith('RX_'):
        parts = body.split(':')
        # RX_C:<rssi>:<snr>:UNICAST:<port>:<hex>
        if len(parts) >= 6:
            return {'type': 'rx', 'window': parts[0][3:], 'rssi': int(parts[1]),
                    'snr': int(parts[2]), 'port': int(parts[4]),
                    'payload': bytes.fromhex(parts[5])}
    return {'type': 'other', 'raw': body}


class Rak3172:
    def __init__(self, ser, clock=time.monotonic):
        self.ser = ser
        self.clock = clock
        self.events = []

    def _readline(self):
        raw = self.ser.readline()
        return raw.decode('ascii', 'replace').strip() if raw else ''

    def command(self, cmd: str, timeout: float = 3.0):
        self.ser.write((cmd + '\r\n').encode())
        deadline = self.clock() + timeout
        lines = []
        while self.clock() < deadline:
            line = self._readline()
            if not line:
                continue
            evt = parse_event(line)
            if evt is not None:
                self.events.append(evt)
            elif line == _TERMINAL_OK:
                return lines
            elif line.startswith('AT_'):
                raise RakError(f'{cmd} -> {line}')
            else:
                lines.append(line)
        raise RakError(f'{cmd} -> timeout')

    def configure_otaa(self, dev_eui: str, app_eui: str, app_key: str) -> None:
        for cmd in ('AT+NWM=1', 'AT+BAND=4', 'AT+NJM=1', 'AT+CLASS=C', 'AT+ADR=1',
                    f'AT+DEVEUI={dev_eui}', f'AT+APPEUI={app_eui}', f'AT+APPKEY={app_key}'):
            self.command(cmd)

    def _wait_event(self, types, timeout):
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            for i, evt in enumerate(self.events):
                if evt['type'] in types:
                    return self.events.pop(i)
            line = self._readline()
            if line:
                evt = parse_event(line)
                if evt is not None:
                    self.events.append(evt)
        return None

    def join(self, timeout: float = 120.0) -> bool:
        self.command('AT+JOIN=1:0:10:8')
        evt = self._wait_event(('joined', 'join_failed'), timeout)
        return evt is not None and evt['type'] == 'joined'

    def send(self, port: int, payload: bytes, timeout: float = 15.0) -> bool:
        self.command(f'AT+SEND={port}:{payload.hex().upper()}')
        evt = self._wait_event(('tx_done', 'tx_failed'), timeout)
        return evt is not None and evt['type'] == 'tx_done'

    def poll_events(self):
        while True:
            line = self._readline()
            if not line:
                break
            evt = parse_event(line)
            if evt is not None:
                self.events.append(evt)
        out, self.events = self.events, []
        return out
```

- [ ] **Step 4:** Test → PASS. **Step 5:** Commit `feat(lora): RAK3172 RUI3 AT driver with queued async events`.

### Task S3: Remote-site uplink service

**Files:** Create `pi/scripts/lora_uplink.py`; Test `pi/tests/test_lora_uplink.py`; Create `pi/config/lora.json.example`.

**Interfaces (produced):** `ZoneAggregator.add(zone, metric, value)`, `.summaries() -> list[ZoneSummary]`, `.reset()`; `route_reading(topic, payload, agg) -> bool`; `AlertLimiter.allow(rule_id, now) -> bool`; `command_publication(evt) -> tuple[str, str] | None`.

- [ ] **Step 1: Failing tests:**

```python
# pi/tests/test_lora_uplink.py
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import lora_payload as lp
import lora_uplink as lu


def test_readings_are_averaged_per_zone_over_the_window():
    agg = lu.ZoneAggregator()
    assert lu.route_reading('greenhouse/zone3/air/temperature', b'20.0', agg)
    assert lu.route_reading('greenhouse/zone3/air/temperature', b'22.0', agg)
    assert lu.route_reading('greenhouse/zone3/soil/moisture', b'40.0', agg)
    assert agg.summaries() == [lp.ZoneSummary(3, 21.0, None, 40.0)]


def test_non_reading_topics_and_non_numeric_zones_are_ignored():
    agg = lu.ZoneAggregator()
    assert not lu.route_reading('greenhouse/weather/temperature', b'30', agg)
    assert not lu.route_reading('greenhouse/Tomatoes/air/temperature', b'30', agg)
    assert not lu.route_reading('greenhouse/zone2/air/temperature', b'nan?', agg)
    assert agg.summaries() == []


def test_reset_starts_a_new_window():
    agg = lu.ZoneAggregator()
    lu.route_reading('greenhouse/zone1/air/humidity', b'55', agg)
    agg.reset()
    assert agg.summaries() == []


def test_alert_limiter_allows_one_per_rule_per_900s():
    lim = lu.AlertLimiter(900)
    assert lim.allow('r1', 0) and not lim.allow('r1', 899) and lim.allow('r1', 900)
    assert lim.allow('r2', 10)


def test_class_c_command_becomes_a_local_actuator_publish():
    evt = {'type': 'rx', 'port': lp.PORT_COMMAND, 'payload': lp.encode_command('pump1', True)}
    assert lu.command_publication(evt) == ('greenhouse/actuators/pump1/set', 'ON')
    assert lu.command_publication({'type': 'rx', 'port': 3, 'payload': b'\x01'}) is None
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `pi/scripts/lora_uplink.py`:

```python
#!/usr/bin/env python3
"""Remote greenhouse -> LoRaWAN uplink (spec 2026-09-14, rev. 2026-09-23).

Averages this site's local readings over report_interval_s and sends one
compact summary uplink; forwards local rule alerts immediately (rate-limited);
turns Class C downlink commands into local actuator publishes. The site keeps
working exactly as before if the gateway is unreachable -- nothing here is on
the local data path.
"""
import json
import os
import sys
import time

import paho.mqtt.client as mqtt
import serial

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import lora_payload as lp
from rak3172 import Rak3172, RakError

CONFIG_PATH = '/etc/greenhouse/lora.json'
LORA_PORT = '/dev/lora'
_METRICS = {('air', 'temperature'): 'temperature', ('air', 'humidity'): 'humidity',
            ('soil', 'moisture'): 'soil'}


class ZoneAggregator:
    def __init__(self):
        self.reset()

    def reset(self):
        self._sums = {}

    def add(self, zone, metric, value):
        z = self._sums.setdefault(zone, {})
        s, n = z.get(metric, (0.0, 0))
        z[metric] = (s + value, n + 1)

    def summaries(self):
        out = []
        for zone in sorted(self._sums):
            m = self._sums[zone]
            mean = lambda k: (m[k][0] / m[k][1]) if k in m else None  # noqa: E731
            out.append(lp.ZoneSummary(zone, mean('temperature'), mean('humidity'), mean('soil')))
        return out


def route_reading(topic, payload, agg) -> bool:
    parts = topic.split('/')
    if len(parts) != 4 or parts[0] != 'greenhouse':
        return False
    metric = _METRICS.get((parts[2], parts[3]))
    zone = lp.zone_number(parts[1])
    if metric is None or zone is None:
        return False
    try:
        agg.add(zone, metric, float(payload))
    except ValueError:
        return False
    return True


class AlertLimiter:
    def __init__(self, min_gap_s):
        self.min_gap_s = min_gap_s
        self.last = {}

    def allow(self, rule_id, now) -> bool:
        prev = self.last.get(rule_id)
        if prev is not None and now - prev < self.min_gap_s:
            return False
        self.last[rule_id] = now
        return True


def command_publication(evt):
    if evt.get('type') != 'rx' or evt.get('port') != lp.PORT_COMMAND:
        return None
    try:
        actuator, on = lp.decode_command(evt['payload'])
    except ValueError:
        return None
    return f'greenhouse/actuators/{actuator}/set', 'ON' if on else 'OFF'


def run() -> None:
    cfg = json.load(open(CONFIG_PATH))
    interval = int(cfg.get('report_interval_s', 600))
    dev = Rak3172(serial.Serial(LORA_PORT, 115200, timeout=0.5))
    dev.configure_otaa(cfg['dev_eui'], cfg['app_eui'], cfg['app_key'])
    while not dev.join():
        print('[lora] join failed -- retrying in 60 s', flush=True)
        time.sleep(60)
    print('[lora] joined', flush=True)

    agg = ZoneAggregator()
    limiter = AlertLimiter(900)
    alerts = []

    def on_message(_c, _u, msg):
        if msg.topic == 'greenhouse/weather/alert':
            try:
                a = json.loads(msg.payload)
                alerts.append((a.get('severity', 'warning'), str(a.get('rule_id') or a.get('type'))))
            except (ValueError, TypeError):
                pass
        elif not msg.retain:
            route_reading(msg.topic, msg.payload, agg)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-lora-uplink')
    client.on_message = on_message
    client.connect('127.0.0.1', 1883, 30)
    for t in ('greenhouse/+/air/temperature', 'greenhouse/+/air/humidity',
              'greenhouse/+/soil/moisture', 'greenhouse/weather/alert'):
        client.subscribe(t, qos=0)
    client.loop_start()

    next_report = time.monotonic() + interval
    while True:
        try:
            while alerts:
                severity, rule_id = alerts.pop(0)
                if limiter.allow(rule_id, time.monotonic()):
                    dev.send(lp.PORT_ALERT, lp.encode_alert(severity, rule_id))
            if time.monotonic() >= next_report:
                for msg in lp.encode_summaries(agg.summaries()):
                    dev.send(lp.PORT_SUMMARY, msg)
                agg.reset()
                next_report += interval
            for evt in dev.poll_events():
                pub = command_publication(evt)
                if pub:
                    client.publish(*pub)
        except RakError as exc:
            print(f'[lora] module error: {exc}', flush=True)
            time.sleep(5)
        time.sleep(1)


if __name__ == '__main__':
    run()
```

`pi/config/lora.json.example`:

```json
{
  "dev_eui": "AC1F09FFFE000001",
  "app_eui": "0000000000000000",
  "app_key": "REPLACE_WITH_32_HEX_FROM_CHIRPSTACK",
  "report_interval_s": 600
}
```

- [ ] **Step 4:** Test → PASS; full suite. **Step 5:** Commit `feat(lora): remote-site uplink service (averaged summaries, alerts, Class C commands)`.

### Task S4: Gateway bridge

**Files:** Create `pi/scripts/lora_gateway_bridge.py`; Test `pi/tests/test_lora_gateway_bridge.py`; Create `pi/config/lora_sites.json.example`.

**Interfaces (produced):** `parse_uplink(topic, raw_json) -> dict | None` (`dev_eui`, `fport`, `data`, `rssi`, `snr`); `uplink_publications(site, up, now) -> list[(topic, payload, retain)]`; `downlink_request(topic, payload, cfg) -> tuple[str, str] | None`.

ChirpStack v4 MQTT integration (chirpstack.io docs): uplink topic `application/<app_id>/device/<dev_eui>/event/up`, JSON with `fPort`, `data` (base64), `rxInfo[]`; downlink topic `application/<app_id>/device/<dev_eui>/command/down` with `{"devEui","confirmed","fPort","data"}`. `dev_eui` is taken from the topic path (hex), not the JSON body.

- [ ] **Step 1: Failing tests:**

```python
# pi/tests/test_lora_gateway_bridge.py
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import lora_gateway_bridge as gb
import lora_payload as lp

CFG = {'application_id': 'app-1', 'sites': {'ac1f09fffe000001': 'north'}}


def _up(fport, data):
    return json.dumps({'fPort': fport, 'data': base64.b64encode(data).decode(),
                       'rxInfo': [{'rssi': -112, 'snr': -7.5}]}).encode()


def test_uplink_is_parsed_from_topic_and_body():
    raw = _up(1, b'\x01\x00')
    up = gb.parse_uplink('application/app-1/device/AC1F09FFFE000001/event/up', raw)
    assert up == {'dev_eui': 'ac1f09fffe000001', 'fport': 1, 'data': b'\x01\x00',
                  'rssi': -112, 'snr': -7.5}


def test_summary_becomes_site_scoped_retained_readings():
    data = lp.encode_summaries([lp.ZoneSummary(3, 21.5, None, 40.0)])[0]
    pubs = gb.uplink_publications('north', {'fport': 1, 'data': data, 'rssi': -112,
                                            'snr': -7.5}, now=1700000000)
    topics = {t: p for t, p, _r in pubs}
    assert topics['greenhouse/sites/north/zone3/air/temperature'] == '21.5'
    assert topics['greenhouse/sites/north/zone3/soil/moisture'] == '40.0'
    assert 'greenhouse/sites/north/zone3/air/humidity' not in topics
    assert json.loads(topics['greenhouse/sites/north/lora'])['rssi'] == -112


def test_alert_becomes_site_alert(monkeypatch):
    pushed = []
    monkeypatch.setattr(gb, '_push', lambda title, body: pushed.append((title, body)))
    pubs = gb.uplink_publications('north', {'fport': 2, 'data': lp.encode_alert('warning', 'dry'),
                                            'rssi': -100, 'snr': 1}, now=1)
    assert any(t == 'greenhouse/sites/north/weather/alert' for t, _p, _r in pubs)
    assert pushed and 'north' in pushed[0][0]


def test_site_command_becomes_chirpstack_downlink():
    topic, body = gb.downlink_request('greenhouse/sites/north/actuators/pump1/set', b'ON', CFG)
    assert topic == 'application/app-1/device/ac1f09fffe000001/command/down'
    msg = json.loads(body)
    assert msg['fPort'] == lp.PORT_COMMAND and msg['confirmed'] is False
    assert lp.decode_command(base64.b64decode(msg['data'])) == ('pump1', True)


def test_unknown_site_command_is_dropped():
    assert gb.downlink_request('greenhouse/sites/south/actuators/pump1/set', b'ON', CFG) is None
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `pi/scripts/lora_gateway_bridge.py`:

```python
#!/usr/bin/env python3
"""ChirpStack <-> greenhouse/sites/<site_id>/... bridge (runs on the gateway Pi).

Remote-site data is published under greenhouse/sites/..., which the existing
hivemq_bridge.py already forwards (greenhouse/#), and which the local site's
single-level '+' subscriptions cannot match -- so the gateway's own greenhouse
dashboard and recorder are unaffected.
"""
import base64
import json
import os
import sys
import time

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import lora_payload as lp

CONFIG_PATH = '/etc/greenhouse/lora_sites.json'
_READING_TOPICS = (('temperature', 'air/temperature'), ('humidity', 'air/humidity'),
                   ('soil', 'soil/moisture'))


def _push(title, body):
    try:
        from push import send_push
        send_push(title, body)
    except Exception as exc:          # a push failure must not stop the bridge
        print(f'[lora-gw] push failed: {exc}', flush=True)


def parse_uplink(topic, raw):
    parts = topic.split('/')
    if len(parts) != 6 or parts[0] != 'application' or parts[2] != 'device' or parts[5] != 'up':
        return None
    try:
        body = json.loads(raw)
        rx = (body.get('rxInfo') or [{}])[0]
        return {'dev_eui': parts[3].lower(), 'fport': int(body['fPort']),
                'data': base64.b64decode(body.get('data') or ''),
                'rssi': rx.get('rssi'), 'snr': rx.get('snr')}
    except (ValueError, KeyError, TypeError):
        return None


def uplink_publications(site, up, now):
    base = f'greenhouse/sites/{site}'
    pubs = [(f'{base}/lora', json.dumps({'rssi': up['rssi'], 'snr': up['snr'], 'ts': int(now)}), True)]
    try:
        if up['fport'] == lp.PORT_SUMMARY:
            for z in lp.decode_summary(up['data']):
                for field, suffix in _READING_TOPICS:
                    v = getattr(z, field)
                    if v is not None:
                        pubs.append((f'{base}/zone{z.zone}/{suffix}', f'{v:.1f}', True))
        elif up['fport'] == lp.PORT_ALERT:
            a = lp.decode_alert(up['data'])
            pubs.append((f'{base}/weather/alert', json.dumps({**a, 'site': site, 'ts': int(now)}), False))
            _push(f'Greenhouse {site}', f'Rule {a["rule_id"]} ({a["severity"]})')
    except ValueError as exc:
        print(f'[lora-gw] bad payload from {site}: {exc}', flush=True)
    return pubs


def downlink_request(topic, payload, cfg):
    parts = topic.split('/')
    if len(parts) != 6 or parts[:2] != ['greenhouse', 'sites'] or parts[3] != 'actuators' \
            or parts[5] != 'set':
        return None
    site, actuator = parts[2], parts[4]
    dev_eui = next((d for d, s in cfg['sites'].items() if s == site), None)
    if dev_eui is None:
        return None
    on = payload.strip().upper() == b'ON'
    body = {'devEui': dev_eui, 'confirmed': False, 'fPort': lp.PORT_COMMAND,
            'data': base64.b64encode(lp.encode_command(actuator, on)).decode()}
    return f'application/{cfg["application_id"]}/device/{dev_eui}/command/down', json.dumps(body)


def run() -> None:
    cfg = json.load(open(CONFIG_PATH))
    cfg['sites'] = {k.lower(): v for k, v in cfg['sites'].items()}

    def on_message(client, _u, msg):
        if msg.topic.startswith('application/'):
            up = parse_uplink(msg.topic, msg.payload)
            site = cfg['sites'].get(up['dev_eui']) if up else None
            if site:
                for topic, payload, retain in uplink_publications(site, up, time.time()):
                    client.publish(topic, payload, retain=retain)
        elif not msg.retain:
            req = downlink_request(msg.topic, msg.payload, cfg)
            if req:
                client.publish(*req)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-lora-gateway')
    client.username_pw_set(cfg['mqtt_user'], cfg['mqtt_password'])
    client.on_message = on_message
    client.connect('127.0.0.1', 1883, 30)
    client.subscribe(f'application/{cfg["application_id"]}/device/+/event/up', qos=1)
    client.subscribe('greenhouse/sites/+/actuators/+/set', qos=1)
    client.loop_forever()


if __name__ == '__main__':
    run()
```

`pi/config/lora_sites.json.example`:

```json
{
  "application_id": "REPLACE_WITH_CHIRPSTACK_APPLICATION_UUID",
  "mqtt_user": "lora-gateway",
  "mqtt_password": "REPLACE",
  "sites": { "AC1F09FFFE000001": "north" }
}
```

- [ ] **Step 4:** Test → PASS; full suite. **Step 5:** Commit `feat(lora): gateway bridge ChirpStack <-> greenhouse/sites/<site>/…`.

### Task S5: Configurable serial port for the ESP32 bridge

**Files:** Modify `pi/scripts/serial_bridge.py:39`; Test `pi/tests/test_serial_bridge.py`.

- [ ] **Step 1: Failing test:**

```python
def test_serial_port_can_be_overridden_by_environment(monkeypatch):
    import importlib
    monkeypatch.setenv('GREENHOUSE_SERIAL_PORT', '/dev/ttyAMA3')
    import serial_bridge
    importlib.reload(serial_bridge)
    try:
        assert serial_bridge.SERIAL_PORT == '/dev/ttyAMA3'
    finally:
        monkeypatch.delenv('GREENHOUSE_SERIAL_PORT')
        importlib.reload(serial_bridge)
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Replace `SERIAL_PORT = '/dev/serial0'` with

```python
# Pi Zero W sites: the one exposed UART. A Pi 4 gateway carrying the RAK2287
# HAT moves the ESP32 bridge to UART3 (the HAT's GPS owns GPIO14/15) and sets
# GREENHOUSE_SERIAL_PORT in the service unit -- see docs/LORAWAN_SETUP.md.
SERIAL_PORT = _os.environ.get('GREENHOUSE_SERIAL_PORT', '/dev/serial0')
```

- [ ] **Step 4:** Test → PASS; full suite. **Step 5:** Commit `feat(pi): serial bridge port configurable (UART3 on a LoRa gateway Pi 4)`.

### Task S6: Services, udev rule, runbook

**Files:** Create `pi/systemd/greenhouse-lora-uplink.service`, `pi/systemd/greenhouse-lora-gateway.service`, `pi/udev/99-greenhouse-lora.rules`, `docs/LORAWAN_SETUP.md`.

- [ ] **Step 1:** `pi/udev/99-greenhouse-lora.rules` (RAK3172-E's CH340 is `1a86:7523`):

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="lora", GROUP="dialout", MODE="0660"
```

- [ ] **Step 2:** `pi/systemd/greenhouse-lora-uplink.service` — copy `greenhouse-serial-bridge.service` verbatim, then change `Description=Greenhouse LoRaWAN uplink (remote site -> shared gateway)`, `ExecStart=/usr/bin/python3 /home/pi/greenhouse/scripts/lora_uplink.py`, and the device comment to `/dev/lora` (keep every sandboxing line; `PrivateDevices` stays unset for the same reason).
- [ ] **Step 3:** `pi/systemd/greenhouse-lora-gateway.service` — copy `greenhouse-serial-bridge.service`, change `Description=Greenhouse LoRaWAN gateway bridge (ChirpStack <-> greenhouse/sites)`, `ExecStart=/usr/bin/python3 /home/pi/greenhouse/scripts/lora_gateway_bridge.py`, `After=network.target mosquitto.service chirpstack.service`, and **add** `PrivateDevices=yes` (it touches no device).
- [ ] **Step 4:** Write `docs/LORAWAN_SETUP.md` containing Tasks H1–H3 below as a runbook (copy the steps verbatim).
- [ ] **Step 5:** Commit `feat(lora): services, udev rule and setup runbook`.

---

## Part H — Hardware and OS bring-up (runbook procedures)

### Task H1: Remote site (per greenhouse)

**BOM:** RAK3172 Evaluation Board, **EU868** variant (`RAK3172-E`, includes CH340 USB-serial) · 868 MHz ~3 dBi antenna (u.FL/SMA per board variant) · micro-USB OTG adapter + micro-USB cable. No SIM, no internet.

- [ ] Plug the RAK3172-E into the Zero W's **inner** micro-USB port (data/OTG; the outer one is power-only) via the OTG adapter.
- [ ] `sudo cp pi/udev/99-greenhouse-lora.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger`; verify `ls -l /dev/lora`.
- [ ] Sanity: `python3 -c "import serial; s=serial.Serial('/dev/lora',115200,timeout=1); s.write(b'AT\r\n'); print(s.read(64))"` → contains `OK`.
- [ ] In ChirpStack (gateway, Task H2): create the device (OTAA, device profile EU868 / LoRaWAN 1.0.x / Class C), copy DevEUI, JoinEUI(AppEUI) and AppKey into `/etc/greenhouse/lora.json` (template `pi/config/lora.json.example`, mode 0600).
- [ ] Install and enable `greenhouse-lora-uplink.service`; `journalctl -u greenhouse-lora-uplink -f` shows `[lora] joined`.

### Task H2: Gateway (one per network)

**BOM:** Raspberry Pi 4 (≥ 2 GB) + official 5 V/3 A PSU · RAK2287 SPI (EU868) + RAK2287/RAK5146 Pi HAT · 868 MHz ~6 dBi outdoor antenna, **mounted as high as possible** (report §21.7: range is set by antenna height, `d ≈ 3.57(√h₁+√h₂)` km) + low-loss coax + lightning arrestor · SIM7600G-H **USB** modem + LTE antenna + M2M SIM · powered USB hub (≥ 2.5 A).

- [ ] Raspberry Pi OS **64-bit**; install the greenhouse stack as on any site (`pi/install.sh`).
- [ ] `/boot/firmware/config.txt`: `dtparam=spi=on` and `dtoverlay=uart3`. Reboot. Rewire the ESP32 bridge: ESP32 GPIO4 (TX) → Pi **GPIO5 / pin 29** (RXD3); ESP32 GPIO5 (RX) ← Pi **GPIO4 / pin 7** (TXD3); GND common. Find the node with `ls -l /dev/ttyAMA*` and set `Environment=GREENHOUSE_SERIAL_PORT=/dev/ttyAMA<N>` in `greenhouse-serial-bridge.service` (Task S5). Verify `journalctl -u greenhouse-serial-bridge` shows heartbeats.
- [ ] Mount the RAK2287 on the Pi HAT, the HAT on the header. Pins used by the HAT (datasheet): SPI0 GPIO8–11, RESET GPIO17, GPIO7, GPS on GPIO14/15, GPS reset GPIO25, standby GPIO12 — none overlap UART3.
- [ ] Install per the ChirpStack Debian/Ubuntu guide: `redis-server`, `chirpstack-concentratord` (SX1302 build, EU868 config, reset pin 17, SPI `/dev/spidev0.0`), `chirpstack-mqtt-forwarder` (Concentratord backend), and ChirpStack **SQLite** build (≥ 4.10.1). Point ChirpStack and the forwarder at the local Mosquitto with a dedicated user; add ACL lines `topic readwrite eu868/#` for the forwarder/ChirpStack user and `topic read application/#` + `topic write application/+/device/+/command/down` + `topic readwrite greenhouse/sites/#` for the `lora-gateway` user.
- [ ] In ChirpStack: add the gateway (EUI from Concentratord log), region eu868; create the application (copy its UUID into `/etc/greenhouse/lora_sites.json`) and one device per remote site (DevEUI → site id in `sites`).
- [ ] Enable `greenhouse-lora-gateway.service`.

### Task H3: LTE uplink on the gateway

- [ ] Modem on the powered hub. With `minicom -D /dev/ttyUSB2` (AT port; confirm with `ls /dev/ttyUSB*`) send `AT+CUSBPIDSWITCH=9011,1,1`; after it re-enumerates, `ip link` shows `usb0`.
- [ ] `nmcli con add type ethernet ifname usb0 con-name lte ipv4.method auto ipv4.route-metric 50` and bring it up; `curl -4 https://www.google.com -o /dev/null -w '%{http_code}'` → 200 over `usb0`.
- [ ] Confirm `greenhouse-hivemq-bridge` reconnects over LTE and that `greenhouse/sites/<site>/…` topics appear on HiveMQ.
- [ ] Static-IP-direct path stays blocked on the carrier question in spec §Open risks.

---

## Part V — Field validation (procedures)

- [ ] **V1 join/uplink:** remote site within 100 m: join ≤ 2 min; a summary appears under `greenhouse/sites/<site>/zone*/…` within one `report_interval_s`; ChirpStack frame log shows the uplink DR.
- [ ] **V2 range:** move the remote antenna to 1, 3, 5 km (line of sight where possible); record `greenhouse/sites/<site>/lora` RSSI/SNR and the ADR-chosen DR at each point. Pass: uplinks every interval with SNR above the SF12 demodulation floor (≈ −20 dB); compare with report §21.7 horizon estimate.
- [ ] **V3 command:** `mosquitto_pub -t greenhouse/sites/<site>/actuators/test/set -m ON` (via HiveMQ) → remote site logs a local publish to `greenhouse/actuators/test/set` within ~10 s (Class C).
- [ ] **V4 alert:** trigger a rule at the remote site → alert on `greenhouse/sites/<site>/weather/alert` and a phone push from the gateway, at most one per rule per 15 min.
- [ ] **V5 isolation:** power off the gateway for 1 h → remote site's local dashboard, history and rules keep working; uplinks resume when it returns (join session kept).
- [ ] **V6 duty cycle:** after 24 h, ChirpStack gateway airtime/hour for each remote device ≤ 36 s (uplink) and gateway downlink airtime ≤ 360 s/hour.

## Self-review notes

- Spec coverage: remote module & wiring (rev. §1) → H1, S2, S3, S6; Pi 4 gateway + ChirpStack (rev. §2) → H2; UART3 remap (rev. §3) → S5, H2; USB modem (rev. §4) → H3; payload budget / "what to send" → S1, S3; alerts as immediate uplinks → S3, S4 (+ push on gateway); Class C downlink → S2, S3, S4; topic isolation → S4; security layering (LoRaWAN session keys, TLS on the cellular hop) → H1/H2 (ChirpStack keys), existing HiveMQ TLS unchanged.
- Out of scope (as in the spec): the Flutter multi-site UI; static-IP-direct.
- Nothing here executes automatically — the user has asked for plans only.
