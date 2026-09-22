# Mesh Silent-Failure Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pi/scripts/serial_bridge.py`'s three silent mesh-frame failure paths (unenrolled MAC, replay drop, decrypt/auth failure) feed the existing `pi/shared/security_log.py` audit-log + push-alert pipeline, instead of only printing to stdout.

**Architecture:** No new subsystem. `pi/shared/security_log.py` already gives every other auth boundary in this project (pairing PIN, camera token, history auth, TLS cert mismatch) a structured JSON audit log at `/var/log/greenhouse-security.log` plus a rate-limited push notification for events in its `ALERTABLE` set (`pi/portal/portal.py` is the reference caller). `serial_bridge.py` has three places in `handle_frame()` where a mesh frame is silently dropped with only a `print()` — this plan wires each of them into `log_security_event()`, and adds the one new event kind that actually warrants a phone push.

**Tech Stack:** Python 3 (Raspberry Pi OS / Bookworm-Trixie), pytest, no new dependencies.

**Spec:** No separate spec file — this is a bounded change validated by the `superpowers:brainstorming` conversation on 2026-09-15 (the exchange that produced this plan). The design summary below is everything an implementer needs; there is no other doc to cross-reference.

**Design summary (from brainstorming):**
- A frame from a MAC not in the Pi's trust store (`nodes.json`) is normal during onboarding (owner has the app open, pairing a new sensor) — log only, **never** push, or every pairing session would spam the owner's phone.
- A replayed `(boot_count, seq)` pair is usually a benign duplicate (e.g. a sensor retry after a dropped ESP-NOW ack) — log only, not alert-worthy by itself.
- A frame from an **already-enrolled** MAC that fails AES-GCM decrypt/auth is the interesting case: it means either a stale/mismatched AppKey in the node's NVS (the exact bug class that silently broke zone2 and zone4 earlier this project) or an actual spoofing attempt. This is the one new `ALERTABLE` kind — it should reach the owner's phone, rate-limited by the existing per-kind cooldown.
- Scope explicitly excludes: any new Flutter/app UI, any change to the mesh wire protocol, and the separate (larger, still-unscoped) end-to-end ACK feature that would let the *sensor itself* learn its reading was accepted — that was intentionally deferred as a separate future architectural piece, not part of this plan.

## Global Constraints

- Python: match the existing project style in `pi/` — no type hints beyond what's already in these files, no new third-party dependencies (this only uses the stdlib-only `security_log` module, already vendored at `pi/shared/security_log.py`).
- Every call site added by this plan must use the **defensive import pattern already established in `pi/portal/portal.py:36-40`** (`try: from security_log import log_security_event except Exception: def log_security_event(*_a, **_kw): return {}`) — logging must never be able to take down mesh frame ingestion.
- Test runner: `python -m pytest pi/tests/ -v` (run from the repo root — this is exactly what CI's "Pi tests (pytest)" job runs, see `.github/workflows/ci.yml:38`). Run this after every task.
- Follow the existing test style in `pi/tests/test_serial_bridge_mesh.py` and `pi/tests/test_security_log.py` (`monkeypatch`, no mocking frameworks, plain asserts).

---

## Task 1: Make `mesh_auth_failure` an alertable security-log event kind

**Files:**
- Modify: `pi/shared/security_log.py:44-49`
- Test: `pi/tests/test_security_log.py`

**Interfaces:**
- Consumes: nothing new — uses `security_log.ALERTABLE` (already exists, a `set[str]`) and `security_log.log_security_event(kind, detail='', source='', alert=None)` (already exists, unchanged signature).
- Produces: the string `'mesh_auth_failure'` as a member of `security_log.ALERTABLE`, consumed by Task 2.

- [ ] **Step 1: Write the failing test**

Add to `pi/tests/test_security_log.py` (after `test_different_event_kinds_have_independent_cooldowns`, before `test_push_failure_does_not_break_the_caller`):

```python
def test_mesh_auth_failure_is_alertable(tmp_path, monkeypatch):
    """An enrolled node's packet failing decrypt/auth means a stale AppKey or
    a spoofing attempt -- either way the owner should be told, not just
    silently logged like a routine dropped frame."""
    _fresh(tmp_path, monkeypatch)
    sent = []
    sys.modules['push'] = type(sys)('push')
    sys.modules['push'].send_push = lambda t, b: sent.append((t, b))
    security_log.log_security_event('mesh_auth_failure', 'bad tag', source='206EF16C9DB0')
    assert len(sent) == 1
    assert '206EF16C9DB0' in sent[0][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pi/tests/test_security_log.py::test_mesh_auth_failure_is_alertable -v`
Expected: FAIL — `assert len(sent) == 1` fails because `sent == []` (the kind isn't in `ALERTABLE` yet, so `_should_alert` returns `False`).

- [ ] **Step 3: Add the new kind to `ALERTABLE`**

In `pi/shared/security_log.py`, the current block (lines 44-49) reads:

```python
ALERTABLE = {
    'pair_lockout',        # someone burned through the PIN attempt limit
    'cam_auth_failure',    # something tried to impersonate the camera
    'history_auth_failure',
    'cert_mismatch',       # a client rejected our cert, or we rejected theirs
}
```

Change it to:

```python
ALERTABLE = {
    'pair_lockout',        # someone burned through the PIN attempt limit
    'cam_auth_failure',    # something tried to impersonate the camera
    'history_auth_failure',
    'cert_mismatch',       # a client rejected our cert, or we rejected theirs
    'mesh_auth_failure',   # an enrolled node's packet failed decrypt/auth --
                            # stale AppKey or spoofing, either way worth a push
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest pi/tests/test_security_log.py::test_mesh_auth_failure_is_alertable -v`
Expected: PASS

- [ ] **Step 5: Run the full security_log test file to check nothing else broke**

Run: `python -m pytest pi/tests/test_security_log.py -v`
Expected: all tests PASS (12 tests including the new one).

- [ ] **Step 6: Commit**

```bash
git add pi/shared/security_log.py pi/tests/test_security_log.py
git commit -m "$(cat <<'EOF'
feat: make mesh_auth_failure an alertable security event

An enrolled node's packet failing decrypt/auth means either a stale
AppKey (the bug class that silently broke zone2/zone4 earlier) or an
actual spoofing attempt. Both warrant a push, unlike a routine dropped
frame.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire `serial_bridge.py`'s silent frame drops into `security_log`

**Files:**
- Modify: `pi/scripts/serial_bridge.py:28-32` (imports), `pi/scripts/serial_bridge.py:251-300` (`handle_frame`)
- Test: `pi/tests/test_serial_bridge_mesh.py`

**Interfaces:**
- Consumes: `log_security_event(kind, detail='', source='', alert=None)` from Task 1 (already exists in `pi/shared/security_log.py`, unmodified signature). Also consumes the existing `mesh_crypto.MeshAuthError` / `mesh_crypto.MeshFormatError` exception types and the existing `accept_replay(state, mac, boot_count, seq) -> bool` function, both already defined in `serial_bridge.py`.
- Produces: `serial_bridge.log_security_event` — a module-level name in `serial_bridge.py` that tests monkeypatch directly (this is how Task 2's tests intercept calls without touching the real log file or the `push` module).

- [ ] **Step 1: Write the failing tests**

Add to `pi/tests/test_serial_bridge_mesh.py`, immediately after `test_a_frame_that_fails_authentication_is_dropped` (currently ending at line 69):

```python
def test_unenrolled_frame_logs_a_security_event(state, monkeypatch):
    logged = []
    monkeypatch.setattr(sb, 'log_security_event', lambda *a, **kw: logged.append((a, kw)))
    other = mc.seal_packet(bytes(16), NET, bytes.fromhex('AABBCCDDEEFF'),
                           seq=1, boot_count=1, flags=0, rank=1, ttl=4,
                           body=mp.pack_body(1, 1, 1, 0, PARENT, 0))
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': other.hex()}, state)
    assert logged == [(('mesh_unenrolled_frame',), {'source': 'AABBCCDDEEFF'})]


def test_replay_drop_logs_a_security_event(state, monkeypatch):
    logged = []
    monkeypatch.setattr(sb, 'log_security_event', lambda *a, **kw: logged.append((a, kw)))
    c = FakeClient()
    sb.handle_frame(c, _frame(), state)   # first delivery: accepted, not logged
    logged.clear()
    sb.handle_frame(c, _frame(), state)   # same (boot_count, seq): replay
    assert logged == [(('mesh_replay_dropped',), {'source': MAC_S})]


def test_auth_failure_logs_a_security_event(state, monkeypatch):
    logged = []
    monkeypatch.setattr(sb, 'log_security_event', lambda *a, **kw: logged.append((a, kw)))
    f = _frame()
    raw = bytearray(bytes.fromhex(f['data']))
    raw[30] ^= 0x01
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': bytes(raw).hex()}, state)
    assert len(logged) == 1
    args, kwargs = logged[0]
    assert args == ('mesh_auth_failure',)
    assert kwargs['source'] == MAC_S
    assert 'detail' in kwargs and kwargs['detail']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pi/tests/test_serial_bridge_mesh.py::test_unenrolled_frame_logs_a_security_event pi/tests/test_serial_bridge_mesh.py::test_replay_drop_logs_a_security_event pi/tests/test_serial_bridge_mesh.py::test_auth_failure_logs_a_security_event -v`
Expected: all 3 FAIL — `sb.log_security_event` doesn't exist yet (`AttributeError` from `monkeypatch.setattr`), since `serial_bridge.py` has no such name at module scope.

- [ ] **Step 3: Add the defensive import**

In `pi/scripts/serial_bridge.py`, the current top-of-file imports (lines 20-32) read:

```python
import json
import os as _os
import sys as _sys
import time

import serial
import paho.mqtt.client as mqtt

_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', 'shared'))
import mesh_crypto
import mesh_packet
import nodes as node_store
```

Change it to:

```python
import json
import os as _os
import sys as _sys
import time

import serial
import paho.mqtt.client as mqtt

_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', 'shared'))
import mesh_crypto
import mesh_packet
import nodes as node_store

try:
    from security_log import log_security_event
except Exception:  # pragma: no cover - logging must never break mesh ingestion
    def log_security_event(*_a, **_kw):
        return {}
```

(This mirrors `pi/portal/portal.py:36-40` exactly — same fallback shape, same reasoning: a broken/missing `security_log` module must never take down frame processing.)

- [ ] **Step 4: Wire the three `handle_frame` branches**

In `pi/scripts/serial_bridge.py`, `handle_frame` (starting at line 251) currently reads:

```python
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
        print(f'[mesh] {mac} failed authentication: {exc}', flush=True)
        return
```

Change the three flagged lines (leave the malformed-frame branch above them and everything from `for metric, (group, topic_metric) in _METRICS.items():` onward untouched):

```python
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
        log_security_event('mesh_unenrolled_frame', source=mac)
        print(f'[mesh] frame from unenrolled {mac} — ignored', flush=True)
        return

    if not accept_replay(state, mac, header.boot_count, header.seq):
        log_security_event('mesh_replay_dropped', source=mac)
        print(f'[mesh] replay from {mac} dropped', flush=True)
        return

    try:
        body = mesh_crypto.open_packet(raw, node.app_key)
    except (mesh_crypto.MeshAuthError, mesh_crypto.MeshFormatError) as exc:
        log_security_event('mesh_auth_failure', detail=str(exc), source=mac)
        print(f'[mesh] {mac} failed authentication: {exc}', flush=True)
        return
```

Note: `mesh_unenrolled_frame` and `mesh_replay_dropped` are deliberately **not** added to `security_log.ALERTABLE` — only `mesh_auth_failure` (Task 1) is. Passing no `alert=` kwarg (the default `None`) is correct for all three calls; `security_log._should_alert` already looks the kind up in `ALERTABLE` and returns `False` for anything not listed, which is exactly the desired behavior for the first two.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest pi/tests/test_serial_bridge_mesh.py -v`
Expected: all tests PASS, including the 3 new ones (the file should now show 13 passing tests total: 10 pre-existing + 3 new).

- [ ] **Step 6: Run the full Pi test suite**

Run: `python -m pytest pi/tests/ -v`
Expected: all tests PASS — this is the same command CI runs (`.github/workflows/ci.yml:38`), so a clean run here means CI's "Pi tests (pytest)" job will be green too.

- [ ] **Step 7: Commit**

```bash
git add pi/scripts/serial_bridge.py pi/tests/test_serial_bridge_mesh.py
git commit -m "$(cat <<'EOF'
feat: log mesh frame drops to the security event pipeline

serial_bridge.py's handle_frame() silently dropped unenrolled-MAC,
replay, and auth-failure frames with only a print() -- invisible
outside a live journalctl tail. Wires all three into the existing
security_log.py audit-log/push pipeline that portal.py already uses
for every other auth boundary (pairing PIN, camera token, TLS cert).

Only mesh_auth_failure pushes to the owner's phone (an enrolled node
failing decrypt means a stale AppKey or spoofing -- the exact bug
class that silently broke zone2/zone4 earlier this project).
mesh_unenrolled_frame and mesh_replay_dropped are log-only: both are
routine during normal sensor pairing and would otherwise spam the
owner's phone every time someone onboards a new sensor.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes (for the implementer, not a step to execute)

- Both tasks together fully cover the brainstorming design: unenrolled → log-only, replay → log-only, auth failure → log + push. There is no fourth failure branch in `handle_frame` (the malformed-frame `except (KeyError, ValueError)` at the very top is intentionally left untouched — it fires on garbage from the UART link itself, not on anything mesh-security-relevant, and adding it here was never part of the brainstormed scope).
- Task ordering matters: Task 1 must land first because Task 2's `test_auth_failure_logs_a_security_event` only checks that `log_security_event` was *called* correctly (via monkeypatch, so it never touches `ALERTABLE`) — but running the two tasks out of order would still leave `mesh_auth_failure` un-alertable in production, which defeats the entire point of this plan. Do not skip or reorder.
- Do not touch `pi/tools/simulator.py`, any firmware `.ino`/`.h` file, or anything under `app/` — this plan is Pi-side only, by design.
