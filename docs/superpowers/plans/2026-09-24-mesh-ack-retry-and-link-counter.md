# Mesh ACK: resend-until-acknowledged + link-only rescan counter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the application-layer MeshAck actually earn its cost. (1) The stale-channel rescan counter counts only link-layer (L2) failures again, so a Pi outage no longer makes every node rescan channels. (2) Every frame a sleepy node sends in a wake cycle that gets no app-level verdict goes back into its buffer and is resent on the next wake. The Pi re-ACKs an authenticated exact duplicate instead of dropping it silently as a replay.

**Architecture:** Pi side: `handle_frame()` authenticates **before** the replay check. A new `replay_verdict()` returns `'new' | 'duplicate' | 'rollback'`, and a duplicate is re-ACKed and publishes nothing. Firmware side: the single-slot ACK wait (`meshWaitingAckSeq`) becomes a small in-flight table in a new Arduino-free header, `mesh_inflight.h`, host-tested with g++ like `mesh_packet.h`. Every frame unicast in a wake (buffer flush + new reading) is recorded there. `meshHandleAck()` marks entries answered. At the end of the wake, unanswered entries go back to the RTC-persisted buffer. The three edge sketches get one rewritten wake-cycle block.

**Tech Stack:** Python 3 (pytest, hand-written fakes), ESP32 Arduino core 3.3.x (arduino-cli), C++11 host harness (MinGW g++).

**Spec:** `docs/superpowers/specs/2026-09-16-mesh-app-ack-design.md` (the ACK being revised). The design delta is in "Design decisions" below. Motivation: on the current code, `confirmed` counts only `MESH_ACK_OK` (`firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:240`). So a Pi or `serial_bridge.py` outage, or a rejected AppKey, drives `g_unconfirmedWakes` to ≥ 2 and forces a full channel rescan on every node every other wake. Meanwhile a missing ACK never causes a resend.

**Branch:** execute on `feature/cart-prereqs-lora-software` (or on `main` after that branch merges). Task 3 edits `meshFlushBuffer()`, which that branch changed (`meshTxTtl()`).

## Design decisions

- **Auth before replay.** A duplicate may only be re-ACKed once it is proven to come from the node's own AppKey. This also fixes a live bug. Today `accept_replay()` runs before `open_packet()`, so a frame with a forged header records its `boot_count`/`seq` in `state['seen']` even though it then fails authentication. Anyone holding the network-wide NetKey (e.g. from one stolen node) can pass the bridge's nettag check, send one frame with `boot_count = 0xFFFFFFFF`, and make every later genuine frame from that node a "rollback" until the Pi restarts.
- **Verdicts:**
  - `rollback`: lower `boot_count`. Logged as `mesh_replay_dropped`, no ACK.
  - `duplicate`: same `boot_count`, `seq` already seen. Re-ACK `ok=True`, no publish, no security event. It is authenticated, so it is the node's own frame: either a resend, or a verbatim replay that yields nothing but one more ACK.
  - `new`: unchanged path.
- **What goes back in the buffer:** every in-flight frame with no verdict. `MESH_ACK_REJECTED` frames are dropped, since resending cannot fix a wrong AppKey. `MESH_ACK_OK` frames are done.
- **Table size:** `MESH_INFLIGHT_MAX = MESH_DATA_BUFFER_SIZE + 1` (a full buffer flush plus the new reading). The buffer stays the bound on how much can be pending. Its ring still drops the oldest reading when full, so a long Pi outage keeps the newest 10 readings.
- **Rescan counter:** `linkOk = delivered || (meshHasParent() && g_lastTxStatus == 1)`. This is exactly the pre-ACK rule.
- **The app-ACK wait now also covers the retry path.** It is bounded by `MESH_APP_ACK_WAIT_MS` (2000 ms) and the 10 s awake backstop.
- **Always-on (non-sleepy) nodes are unchanged.** `meshSendReading()` clears the table at its start, so they never accumulate entries and never retry (today's behavior).
- **No wire-format or bridge change.** The MeshAck stays 20 bytes. Relays already forward a next-wake re-ACK: their ACK dedup window is `MESH_DEDUP_WINDOW_MS` (30 s), and `static_assert(MESH_DEDUP_WINDOW_MS < MESH_SLEEP_INTERVAL_MS)` in `mesh_config.h` holds.

## Global Constraints

- **Deploy order:** Pi first (Task 1), then reflash nodes (Task 3). With an old Pi, resent duplicates are never ACKed, so they cycle through the buffer until the ring drops them.
- `MESH_APP_ACK_WAIT_MS` stays `2000UL`, `MESH_DATA_BUFFER_SIZE` stays `10`, and `MESH_WAKE_MAX_AWAKE_MS` stays `10000UL`. Do not change `MESH_DEDUP_WINDOW_MS` / `MESH_SLEEP_INTERVAL_MS`.
- The MeshAck wire format (20 bytes) and `bridge_esp32.ino` are untouched.
- Pi style: as in `pi/scripts/serial_bridge.py`. Tests use hand-written fakes; no mocking frameworks.
- Pi tests: `python -m pytest pi/tests/ -q`. The Windows-only `test_nodes.py::test_store_is_written_0600…` failure is pre-existing.
- Host C++ tests need MinGW on PATH: `export PATH="/c/Users/billy/AppData/Local/Microsoft/WinGet/Packages/BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/mingw64/bin:/c/Users/billy/bin:$PATH"`.
- Firmware compile (all four, from `firmware/`), `A=/c/Users/billy/tools/arduino-cli/arduino-cli.exe`, `C=/c/Users/billy/tools/arduino-cli/arduino-cli.yaml`:
  `$A --config-file $C compile --fqbn esp32:esp32:esp32c3 edge_node_esp32_c3`, `… esp32c3 fake_edge_node_esp32_c3`, `… esp32c3 bridge_esp32`, `… --fqbn esp32:esp32:esp32 edge_node_esp32`.

## Review Focus

1. **Pi restarts while a node is resending.** `state['seen']` is in memory only, so the resend of an already-accepted reading is `new` again. It is republished once, and the retained "current" value briefly shows the older reading until the next wake. Accepted and documented; Task 1 pins it with a test so it is a known behavior, not an accident.
2. **Captured authentic frame replayed verbatim.** It now yields one re-ACK instead of a security event. Expected: no publish, no security event, one ACK. Pinned in Task 1.
3. **Forged header with a huge `boot_count` that fails auth.** Expected: the node's next genuine frame is still accepted. Pinned in Task 1; this test fails on today's code.
4. **ACK for a seq that is not in flight** (a late ACK from a previous wake, or another node's seq) must change nothing, and a second identical ACK must be harmless. Pinned in Task 2.
5. **Flush stops early** because `esp_now_send` refused or the table is full. Frames not handed to the radio must stay in the buffer, not be lost and not be double-counted. Pinned in Task 2 (full table returns `false`); Task 3's flush adds to the table only after a successful send.

---

### Task 1: Pi — authenticate first, replay verdict, re-ACK duplicates

**Files:**
- Modify: `pi/scripts/serial_bridge.py` (`accept_replay` at ~243 → `replay_verdict`; `handle_frame` at ~261)
- Test: `pi/tests/test_serial_bridge_mesh.py`

**Interfaces:**
- Produces: `replay_verdict(state, mac: str, boot_count: int, seq: int) -> str` returning `'new' | 'duplicate' | 'rollback'`. `accept_replay` is removed (its only caller is `handle_frame`).
- Consumes: the existing `send_ack(ser, mac, seq, ok, ttl=None)`, `_ack_ttl(rank)`, `mesh_crypto.open_packet`.

- [ ] **Step 1: Update the three existing replay-window tests and the two replay/ack tests, and add the new tests.** In `pi/tests/test_serial_bridge_mesh.py`:

Replace `test_replay_drop_logs_a_security_event`, `test_replayed_seq_under_the_same_boot_count_is_rejected`, `test_a_lower_boot_count_is_rejected_as_a_rollback`, `test_a_new_boot_count_resets_the_seq_window` and `test_unenrolled_and_replay_drops_do_not_send_an_ack` with:

```python
def test_a_repeated_seq_under_the_same_boot_count_is_a_duplicate(state):
    assert sb.replay_verdict(state, MAC_S, boot_count=1, seq=5) == 'new'
    assert sb.replay_verdict(state, MAC_S, boot_count=1, seq=5) == 'duplicate'


def test_a_lower_boot_count_is_a_rollback(state):
    assert sb.replay_verdict(state, MAC_S, boot_count=4, seq=1) == 'new'
    assert sb.replay_verdict(state, MAC_S, boot_count=3, seq=99) == 'rollback'


def test_a_new_boot_count_resets_the_seq_window(state):
    assert sb.replay_verdict(state, MAC_S, boot_count=1, seq=9) == 'new'
    assert sb.replay_verdict(state, MAC_S, boot_count=2, seq=9) == 'new'


def test_a_resent_duplicate_is_re_acked_not_logged_not_republished(state, monkeypatch):
    logged, acked = [], []
    monkeypatch.setattr(sb, 'log_security_event', lambda *a, **kw: logged.append((a, kw)))
    monkeypatch.setattr(sb, 'send_ack', lambda *a, **kw: acked.append((a, kw)))
    ser = object()
    c = FakeClient()
    sb.handle_frame(c, _frame(), state, ser)      # first delivery: accepted
    published_before = list(c.published)
    logged.clear(); acked.clear()
    sb.handle_frame(c, _frame(), state, ser)      # identical frame: the node never got our ack
    assert acked == [((ser, MAC_S, 1), {'ok': True, 'ttl': 3})]
    assert logged == []
    assert c.published == published_before        # nothing republished


def test_a_boot_count_rollback_logs_and_is_not_acked(state, monkeypatch):
    logged, acked = [], []
    monkeypatch.setattr(sb, 'log_security_event', lambda *a, **kw: logged.append((a, kw)))
    monkeypatch.setattr(sb, 'send_ack', lambda *a, **kw: acked.append((a, kw)))
    ser = object()
    c = FakeClient()
    sb.handle_frame(c, _frame(seq=1, boot=2), state, ser)
    logged.clear(); acked.clear()
    sb.handle_frame(c, _frame(seq=5, boot=1), state, ser)
    assert logged == [(('mesh_replay_dropped',), {'source': MAC_S})]
    assert acked == []


def test_unenrolled_frames_are_not_acked(state, monkeypatch):
    acked = []
    monkeypatch.setattr(sb, 'send_ack', lambda *a, **kw: acked.append((a, kw)))
    other = mc.seal_packet(bytes(16), NET, bytes.fromhex('AABBCCDDEEFF'),
                           seq=1, boot_count=1, flags=0, rank=1, ttl=4,
                           body=mp.pack_body(1, 1, 1, 0, PARENT, 0))
    sb.handle_frame(FakeClient(), {'type': 'frame', 'data': other.hex()}, state, object())
    assert acked == []


def test_a_forged_frame_cannot_poison_the_replay_window(state):
    # Right MAC and a huge boot_count, but sealed with the wrong AppKey: it must
    # fail authentication BEFORE the replay window ever records its boot_count.
    forged = mc.seal_packet(bytes(16), NET, MAC, seq=1, boot_count=0xFFFFFFFF,
                            flags=0, rank=1, ttl=4,
                            body=mp.pack_body(1, 1, 1, 0, PARENT, 0))
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': forged.hex()}, state)
    c.published.clear()
    sb.handle_frame(c, _frame(seq=2, boot=1), state)   # the node's next genuine frame
    assert any(t.endswith('temperature') for t, _, _ in c.published)


def test_after_a_pi_restart_a_resend_is_accepted_again(state):
    # Documented limitation (plan Review Focus #1): the replay window lives in
    # memory, so a resend that crosses a Pi restart is treated as new.
    c = FakeClient()
    sb.handle_frame(c, _frame(), state)
    state['seen'].clear()                              # what a restart does
    c.published.clear()
    sb.handle_frame(c, _frame(), state)
    assert any(t.endswith('temperature') for t, _, _ in c.published)
```

- [ ] **Step 2: Run and confirm the failures.**
Run: `python -m pytest pi/tests/test_serial_bridge_mesh.py -q`
Expected: FAIL. `replay_verdict` does not exist (AttributeError). `test_a_forged_frame_cannot_poison_the_replay_window` fails on the published assertion.

- [ ] **Step 3: Implement.** In `pi/scripts/serial_bridge.py`, replace `accept_replay` with:

```python
def replay_verdict(state, mac: str, boot_count: int, seq: int) -> str:
    """Classify an AUTHENTICATED frame against this node's replay window.

    No node in this system has a clock, so freshness is (boot_count, seq):
    a higher boot_count always wins and resets the window (a cold boot
    legitimately restarts seq at 0); a lower one is a rollback. A seq already
    seen under the current boot_count is a duplicate -- after authentication
    that means the node resending a frame whose ack it never heard (or a
    verbatim replay of it, which gains an attacker nothing but one more ack).
    Only call this after open_packet() succeeded: an unauthenticated header
    must never move the window.
    """
    last_boot, seen = state['seen'].get(mac, (-1, set()))
    if boot_count < last_boot:
        return 'rollback'
    if boot_count > last_boot:
        state['seen'][mac] = (boot_count, {seq})
        return 'new'
    if seq in seen:
        return 'duplicate'
    seen.add(seq)
    return 'new'
```

In `handle_frame`, delete the block

```python
    if not accept_replay(state, mac, header.boot_count, header.seq):
        log_security_event('mesh_replay_dropped', source=mac)
        print(f'[mesh] replay from {mac} dropped', flush=True)
        return

```

and insert this immediately **after** the whole `try: body = mesh_crypto.open_packet(...) except ...: ... return` block, before the `for metric, (group, topic_metric) in _METRICS.items():` loop:

```python
    verdict = replay_verdict(state, mac, header.boot_count, header.seq)
    if verdict == 'rollback':
        log_security_event('mesh_replay_dropped', source=mac)
        print(f'[mesh] replay from {mac} dropped (boot_count rollback)', flush=True)
        return
    if verdict == 'duplicate':
        # The node resent a frame we already accepted -- our first ack never
        # reached it. Re-ack so it stops resending; publish nothing, the
        # reading is already out.
        if ser is not None:
            send_ack(ser, mac, header.seq, ok=True, ttl=_ack_ttl(header.rank))
        print(f'[mesh] duplicate from {mac} seq {header.seq} — re-acked', flush=True)
        return

```

- [ ] **Step 4: Run the file, then the full suite.**
Run: `python -m pytest pi/tests/test_serial_bridge_mesh.py -q` → all pass.
Run: `python -m pytest pi/tests/ -q` → only the pre-existing Windows 0600 failure.
Run: `grep -rn "accept_replay" pi/` → no matches.

- [ ] **Step 5: Commit.**

```bash
git add pi/scripts/serial_bridge.py pi/tests/test_serial_bridge_mesh.py
git commit -m "fix(pi): authenticate before the replay check; re-ack duplicates

A forged header could move a node's replay window before failing auth,
turning every later genuine frame into a rollback. A resent frame whose
ack was lost is now re-acked instead of dropped as a replay."
```

---

### Task 2: Firmware — in-flight table (pure, host-tested)

**Files:**
- Create: `firmware/libraries/GreenhouseMesh/mesh_inflight.h`
- Create: `firmware/test/host/inflight_test.cpp`
- Modify: `firmware/test/host/Makefile`
- Create: `pi/tests/test_firmware_inflight.py`

**Interfaces:**
- Produces (used by Task 3): `MESH_ACK_OK` (1) and `MESH_ACK_REJECTED` (2), moved here from `mesh_node.h`; `MESH_INFLIGHT_MAX`; `MeshInFlight`; `meshPacketSeq(const uint8_t*)`; `meshInFlightClear(MeshInFlight*)`; `bool meshInFlightAdd(MeshInFlight*, const uint8_t* pkt)`; `bool meshInFlightAnswer(MeshInFlight*, uint16_t seq, uint8_t status)`; `int meshInFlightPending(const MeshInFlight*)`; `typedef void (*MeshRequeueFn)(const uint8_t* pkt)`; `int meshInFlightDrain(MeshInFlight*, MeshRequeueFn, int* okOut, int* rejectedOut)`.

- [ ] **Step 1: Write the host test** `firmware/test/host/inflight_test.cpp`:

```cpp
// firmware/test/host/inflight_test.cpp
// Host-side unit test for mesh_inflight.h. Exit code 0 + "ALL PASS" = green.
#include <cstdio>
#include <cstring>
#include "../../libraries/GreenhouseMesh/mesh_inflight.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static uint8_t requeued[MESH_INFLIGHT_MAX][MESH_PACKET_LEN];
static int requeuedCount = 0;
static void captureRequeue(const uint8_t* pkt) {
  memcpy(requeued[requeuedCount++], pkt, MESH_PACKET_LEN);
}

static void makePkt(uint8_t* pkt, uint16_t seq) {
  memset(pkt, 0xAB, MESH_PACKET_LEN);
  pkt[7] = (uint8_t)(seq & 0xFF);          // seq is little-endian at bytes 7..8
  pkt[8] = (uint8_t)(seq >> 8);
}

int main() {
  MeshInFlight f;
  meshInFlightClear(&f);
  uint8_t p[MESH_PACKET_LEN];

  makePkt(p, 0x1234);
  CHECK(meshPacketSeq(p) == 0x1234);

  // Empty table: nothing pending, drain requeues nothing.
  CHECK(meshInFlightPending(&f) == 0);

  // Three frames in flight.
  for (uint16_t s = 10; s < 13; s++) { makePkt(p, s); CHECK(meshInFlightAdd(&f, p)); }
  CHECK(meshInFlightPending(&f) == 3);

  // ACK for an unknown seq changes nothing.
  CHECK(!meshInFlightAnswer(&f, 99, MESH_ACK_OK));
  CHECK(meshInFlightPending(&f) == 3);

  // OK for 10, REJECTED for 11; a second identical ACK is harmless.
  CHECK(meshInFlightAnswer(&f, 10, MESH_ACK_OK));
  CHECK(meshInFlightAnswer(&f, 10, MESH_ACK_OK));
  CHECK(meshInFlightAnswer(&f, 11, MESH_ACK_REJECTED));
  CHECK(meshInFlightPending(&f) == 1);

  // Drain: only the unanswered one (12) is requeued; counts reported; table empty.
  int ok = -1, rej = -1;
  requeuedCount = 0;
  int n = meshInFlightDrain(&f, captureRequeue, &ok, &rej);
  CHECK(n == 1);
  CHECK(requeuedCount == 1);
  CHECK(meshPacketSeq(requeued[0]) == 12);
  CHECK(ok == 1);
  CHECK(rej == 1);
  CHECK(meshInFlightPending(&f) == 0);
  CHECK(f.count == 0);

  // Drain keeps send order and accepts NULL out-params.
  for (uint16_t s = 20; s < 23; s++) { makePkt(p, s); meshInFlightAdd(&f, p); }
  requeuedCount = 0;
  CHECK(meshInFlightDrain(&f, captureRequeue, NULL, NULL) == 3);
  CHECK(meshPacketSeq(requeued[0]) == 20);
  CHECK(meshPacketSeq(requeued[2]) == 22);

  // Full table refuses more and keeps what it has.
  meshInFlightClear(&f);
  for (int i = 0; i < MESH_INFLIGHT_MAX; i++) { makePkt(p, (uint16_t)(100 + i)); CHECK(meshInFlightAdd(&f, p)); }
  makePkt(p, 999);
  CHECK(!meshInFlightAdd(&f, p));
  CHECK(meshInFlightPending(&f) == MESH_INFLIGHT_MAX);

  if (failures == 0) printf("ALL PASS\n");
  return failures == 0 ? 0 : 1;
}
```

- [ ] **Step 2: Add the Makefile target and the pytest wrapper.** Replace `firmware/test/host/Makefile` with:

```make
# firmware/test/host/Makefile
CXXFLAGS = -std=c++11 -Wall -Wextra -Werror -O1

all: layout_vectors inflight_test

layout_vectors: layout_vectors.cpp ../../libraries/GreenhouseMesh/mesh_packet.h
	$(CXX) $(CXXFLAGS) -o $@ layout_vectors.cpp

inflight_test: inflight_test.cpp ../../libraries/GreenhouseMesh/mesh_inflight.h ../../libraries/GreenhouseMesh/mesh_packet.h
	$(CXX) $(CXXFLAGS) -o $@ inflight_test.cpp

.PHONY: all clean
clean:
	rm -f layout_vectors inflight_test
```

Create `pi/tests/test_firmware_inflight.py`:

```python
# pi/tests/test_firmware_inflight.py
"""Runs the firmware's in-flight ACK table unit test (plain C++, no Arduino).

Skips when g++ is unavailable, same policy as test_firmware_layout.py.
"""
import os
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOST_DIR = os.path.normpath(os.path.join(HERE, '..', '..', 'firmware', 'test', 'host'))


def test_inflight_table_host_unit_test_passes():
    try:
        rc = subprocess.call(['g++', '--version'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        rc = 1
    if rc != 0:
        pytest.skip('g++ not available')
    subprocess.check_call(['make', '-s', 'inflight_test'], cwd=HOST_DIR)
    result = subprocess.run([os.path.join(HOST_DIR, 'inflight_test')],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ALL PASS' in result.stdout
```

- [ ] **Step 3: Run and confirm the failure** (with the MinGW PATH from Global Constraints).
Run: `python -m pytest pi/tests/test_firmware_inflight.py -v`
Expected: FAIL. `make` errors because `mesh_inflight.h` does not exist.

- [ ] **Step 4: Implement** `firmware/libraries/GreenhouseMesh/mesh_inflight.h`:

```cpp
// firmware/libraries/GreenhouseMesh/mesh_inflight.h
#pragma once
// ── Frames awaiting the Pi's app-level verdict ─────────────────────────────────
// Every sealed frame a sleepy node unicasts during one wake (buffer flush +
// the new reading) is recorded here. meshHandleAck() marks entries answered;
// at the end of the wake anything still unanswered goes back into the
// RTC-persisted buffer and is resent next wake (the Pi re-acks an exact
// duplicate without republishing it). REJECTED entries are dropped: resending
// cannot fix a wrong AppKey. Deliberately free of Arduino/ESP-NOW includes so
// firmware/test/host can unit-test it with plain g++.

#include <stdint.h>
#include <string.h>
#include "mesh_packet.h"
#include "mesh_config.h"

#define MESH_ACK_OK        1      // 0 is deliberately unused: an all-zero or
#define MESH_ACK_REJECTED  2      // uninitialized status is never mistaken
                                  // for a valid outcome

#define MESH_INFLIGHT_PENDING 0
#define MESH_INFLIGHT_MAX     (MESH_DATA_BUFFER_SIZE + 1)  // full flush + the new reading

typedef struct {
  uint16_t seq;
  uint8_t  status;                  // MESH_INFLIGHT_PENDING, MESH_ACK_OK or MESH_ACK_REJECTED
  uint8_t  pkt[MESH_PACKET_LEN];    // exact bytes sent, so a resend is byte-identical
} MeshInFlightEntry;

typedef struct {
  MeshInFlightEntry e[MESH_INFLIGHT_MAX];
  int count;
} MeshInFlight;

typedef void (*MeshRequeueFn)(const uint8_t* pkt);

// seq lives little-endian at header bytes 7..8 (mesh_packet.h meshPackHeader).
static inline uint16_t meshPacketSeq(const uint8_t* pkt) {
  return (uint16_t)(pkt[7] | ((uint16_t)pkt[8] << 8));
}

static inline void meshInFlightClear(MeshInFlight* f) { f->count = 0; }

// false = table full; the caller must keep the frame in its own buffer.
static inline bool meshInFlightAdd(MeshInFlight* f, const uint8_t* pkt) {
  if (f->count >= MESH_INFLIGHT_MAX) return false;
  MeshInFlightEntry* x = &f->e[f->count++];
  x->seq    = meshPacketSeq(pkt);
  x->status = MESH_INFLIGHT_PENDING;
  memcpy(x->pkt, pkt, MESH_PACKET_LEN);
  return true;
}

// Idempotent: the same ack can arrive via two relays. Returns whether seq
// matched a frame in flight (a late ack from an earlier wake matches nothing).
static inline bool meshInFlightAnswer(MeshInFlight* f, uint16_t seq, uint8_t status) {
  for (int i = 0; i < f->count; i++) {
    if (f->e[i].seq == seq) { f->e[i].status = status; return true; }
  }
  return false;
}

static inline int meshInFlightPending(const MeshInFlight* f) {
  int n = 0;
  for (int i = 0; i < f->count; i++) if (f->e[i].status == MESH_INFLIGHT_PENDING) n++;
  return n;
}

// Hands every unanswered frame to requeue() in send order, reports how many
// were accepted / rejected, empties the table. Returns the number requeued.
static inline int meshInFlightDrain(MeshInFlight* f, MeshRequeueFn requeue,
                                    int* okOut, int* rejectedOut) {
  int requeued = 0, ok = 0, rejected = 0;
  for (int i = 0; i < f->count; i++) {
    if (f->e[i].status == MESH_ACK_OK)            ok++;
    else if (f->e[i].status == MESH_ACK_REJECTED) rejected++;
    else { requeue(f->e[i].pkt); requeued++; }
  }
  f->count = 0;
  if (okOut)       *okOut = ok;
  if (rejectedOut) *rejectedOut = rejected;
  return requeued;
}
```

Before relying on it, check that `mesh_config.h` compiles on a host (it must have no Arduino includes). Run `grep -n "#include" firmware/libraries/GreenhouseMesh/mesh_config.h`. If it includes anything Arduino-specific, do not include it; instead add `#ifndef MESH_DATA_BUFFER_SIZE` / `#define MESH_DATA_BUFFER_SIZE 10` / `#endif` in `mesh_inflight.h` and note it in the report.

- [ ] **Step 5: Run the test and the layout test.**
Run: `python -m pytest pi/tests/test_firmware_inflight.py pi/tests/test_firmware_layout.py -v` → PASS (not skipped; if skipped, PATH is missing MinGW).

- [ ] **Step 6: Commit.**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_inflight.h firmware/test/host/inflight_test.cpp firmware/test/host/Makefile pi/tests/test_firmware_inflight.py
git commit -m "feat(mesh): in-flight app-ack table (host-tested, Arduino-free)"
```

---

### Task 3: Firmware — wire the table in; link-only rescan counter

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_node.h`
- Modify: `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` (wake block ~189–243)
- Modify: `firmware/edge_node_esp32/edge_node_esp32.ino` (same block ~181–235)
- Modify: `firmware/fake_edge_node_esp32_c3/fake_edge_node_esp32_c3.ino` (same block ~233–287)

**Interfaces:**
- Consumes: everything from Task 2's `mesh_inflight.h`.
- Produces (sketch-facing, replaces `meshArmAckWait`/`meshAckResult`/`meshRequeueLastReading`): `int meshInFlightPendingCount()`, `void meshRequeueInFlight()`, `void meshSettleInFlight()`.

- [ ] **Step 1: `mesh_node.h` — includes and constants.**
  - Add `#include "mesh_inflight.h"` after `#include "mesh_packet.h"`.
  - Delete the `MESH_ACK_OK` / `MESH_ACK_REJECTED` defines and their comment from the MeshAck block; they now come from `mesh_inflight.h`.

- [ ] **Step 2: `mesh_node.h` — state.** Replace the single-wait state (the comment starting `// Set by meshSendReading() right before it unicasts…` and the three `meshWaitingAck*` variables) with:

```cpp
// Every frame unicast this wake, awaiting the Pi's verdict (mesh_inflight.h).
// RAM only: anything unanswered is moved back into the RTC buffer before
// sleep by meshSettleInFlight(), so nothing here needs to survive deep sleep.
static MeshInFlight meshInFlight;
```

Delete `meshLastPkt` and `meshLastPktValid` (their only users are `meshSendReading()` and `meshRequeueLastReading()`, both rewritten below). Delete `meshArmAckWait()` and `meshAckResult()`.

- [ ] **Step 3: `mesh_node.h` — `meshHandleAck()`.** Replace

```cpp
  if (meshMacEqual(a.target_mac, meshSelfMac) && a.seq == meshWaitingAckSeq) {
    // Idempotent overwrite — this ack can legitimately arrive more than once
    // (the same broadcast heard via two different relays), and a second
    // identical write here must be harmless, never a counter/toggle.
    meshWaitingAckStatus = a.status;
    meshWaitingAckSeen   = true;
  }
```

with

```cpp
  if (meshMacEqual(a.target_mac, meshSelfMac)) {
    // Idempotent (the same ack can arrive via two relays); a late ack from
    // an earlier wake matches nothing in flight and changes nothing.
    meshInFlightAnswer(&meshInFlight, a.seq, a.status);
  }
```

- [ ] **Step 4: `mesh_node.h` — flush and send.** Replace `meshFlushBuffer()` with:

```cpp
static void meshFlushBuffer() {
  while (meshBufCount > 0 && meshHasParent_ && meshInFlight.count < MESH_INFLIGHT_MAX) {
    meshBuf[meshBufHead][15] = meshTxTtl();
    if (!meshUnicastToParent(meshBuf[meshBufHead])) break;   // stays buffered
    meshInFlightAdd(&meshInFlight, meshBuf[meshBufHead]);    // room checked above
    meshBufHead = (meshBufHead + 1) % MESH_DATA_BUFFER_SIZE;
    meshBufCount--;
  }
  if (meshBufCount == 0) meshBufHead = 0;
}
```

In `meshSendReading()`:
- Add `meshInFlightClear(&meshInFlight);` as the first statement, before `if (!meshLoadKeys())`. A comment explains: "an always-on node never settles, so each reading starts a fresh table (no retry — unchanged behavior); a sleepy node calls this once per wake, on an already-empty table".
- Delete the two lines `memcpy(meshLastPkt, packet, MESH_PACKET_LEN);` and `meshLastPktValid = true;`.
- In the unrouted branch, delete `meshLastPktValid = false;`.
- Replace the tail from `meshArmAckWait(...)` (including its trailing comment lines) through `meshUnicastToParent(packet);` with:

```cpp
  if (!meshInFlightAdd(&meshInFlight, packet)) {   // cannot happen: MAX = buffer + 1
    meshBufferPush(packet);
    return;
  }
  meshUnicastToParent(packet);
```

- [ ] **Step 5: `mesh_node.h` — sketch-facing helpers.** Replace `meshRequeueLastReading()` with:

```cpp
static int meshInFlightPendingCount() { return meshInFlightPending(&meshInFlight); }

// Nothing sent so far is known to have arrived (e.g. the parent just failed):
// put every unanswered frame back in the buffer so a re-flush resends it.
static void meshRequeueInFlight() {
  meshInFlightDrain(&meshInFlight, meshBufferPush, NULL, NULL);
}

// End of a wake: report the Pi's verdicts, requeue anything unanswered.
static void meshSettleInFlight() {
  int ok = 0, rejected = 0;
  int requeued = meshInFlightDrain(&meshInFlight, meshBufferPush, &ok, &rejected);
  Serial.printf("[wake] app ack: %d accepted, %d rejected%s, %d unanswered -> resend next wake\n",
                ok, rejected, rejected ? " (check AppKey)" : "", requeued);
}
```

Check: `grep -n "meshWaitingAck\|meshAckResult\|meshArmAckWait\|meshLastPkt\|meshRequeueLastReading" firmware -r` → matches only in the three sketches, which Step 6 fixes.

- [ ] **Step 6: The three sketches.** In each of `edge_node_esp32_c3.ino`, `edge_node_esp32.ino` and `fake_edge_node_esp32_c3.ino`, replace the block that starts at `bool delivered = sendWithConfirm(&r, deadline);` and ends at `else if (g_unconfirmedWakes < 250) g_unconfirmedWakes++;` with exactly the following. Keep the `goToSleep(ch);` line that follows it. If a sketch's block differs from the C3 one in anything other than line numbers, stop and report it.

```cpp
  bool delivered = sendWithConfirm(&r, deadline);
  Serial.printf("[wake] delivered=%d hasParent=%d\n", delivered, meshHasParent());

  if (!delivered && millis() < deadline) {
    if (meshHasParent()) meshDropParent("wake tx unconfirmed");
    meshRequeueInFlight();   // nothing sent so far is known to have arrived

    uint32_t listenStart = millis();
    while (!meshHasParent() && millis() - listenStart < MESH_WAKE_DISCOVERY_MS &&
           millis() < deadline) {
      delay(10);
    }
    if (meshHasParent()) {
      g_lastTxStatus = -1;
      meshFlushBuffer();
      uint32_t confirmStart = millis();
      while (g_lastTxStatus == -1 && millis() - confirmStart < MESH_TX_CONFIRM_WAIT_MS &&
             millis() < deadline) {
        delay(5);
      }
    } else {
      Serial.println("[wake] still unrouted — reading stays buffered");
    }
  }

  // Link layer only: "is my parent / channel still good?" -- the question the
  // stale-channel self-heal (g_unconfirmedWakes >= 2 -> full rescan) answers.
  // Whether the Pi accepted the reading is a different question with a
  // different remedy (resend next wake, below); a Pi outage or a rejected
  // AppKey must never trigger a channel rescan.
  bool linkOk = delivered || (meshHasParent() && g_lastTxStatus == 1);
  if (linkOk) g_unconfirmedWakes = 0;
  else if (g_unconfirmedWakes < 250) g_unconfirmedWakes++;

  // App level: wait (bounded) for the Pi's verdict on everything sent this
  // wake; whatever stays unanswered goes back in the buffer for next wake.
  uint32_t ackWaitStart = millis();
  while (meshInFlightPendingCount() > 0 &&
         millis() - ackWaitStart < MESH_APP_ACK_WAIT_MS &&
         millis() < deadline) {
    delay(5);
  }
  meshSettleInFlight();
```

- [ ] **Step 7: Compile all four sketches** (commands in Global Constraints). Expected: each prints `Sketch uses …`, with no `error`. Then run `grep -rn "meshWaitingAck\|meshAckResult\|meshArmAckWait\|meshLastPkt\|meshRequeueLastReading" firmware` → no matches.

- [ ] **Step 8: Run the Pi suite** (the host tests include `mesh_inflight.h` through Task 2's harness): `python -m pytest pi/tests/ -q` → only the pre-existing Windows failure.

- [ ] **Step 9: Commit.**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_node.h firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino firmware/edge_node_esp32/edge_node_esp32.ino firmware/fake_edge_node_esp32_c3/fake_edge_node_esp32_c3.ino
git commit -m "feat(mesh): resend unacknowledged readings next wake; rescan counter is link-only

A Pi outage or a rejected AppKey no longer forces a full channel rescan
on every node every other wake, and a reading the Pi never acknowledged
is resent from the RTC buffer instead of being lost."
```

- [ ] **Step 10 (hardware, optional — only if the user runs it):** use one sleepy C3 node with `MESH_SLEEP_INTERVAL_MS` at the 60 s test value.
  - Run `sudo systemctl stop greenhouse-serial-bridge` on the Pi. Over 3 wakes the node's serial log shows `0 accepted, 0 rejected, N unanswered -> resend next wake`, with N growing, and **no** channel rescan.
  - Start the service again. The next wake logs `N accepted` and the Pi log shows the older readings accepted.
  - Stop the service once more for a single wake, start it again, and check that `journalctl -u greenhouse-serial-bridge` shows `duplicate from … — re-acked` whenever the first ACK was lost.

---

### Task 4: Docs

**Files:**
- Modify: `docs/superpowers/specs/2026-09-16-mesh-app-ack-design.md` (append a section)
- Modify: `HANDOFF.md` (one bullet in the newest TL;DR)
- Report `docs/GreenHouse_Report.docx` §6.10: the controller edits it with the docx tooling, not the implementer.

- [ ] **Step 1:** Append to the ACK spec:

```markdown
## Revision 2026-09-24 — resend until acknowledged; link-only rescan counter

- The stale-channel rescan counter (`g_unconfirmedWakes`) counts link-layer
  failures only again. The app ACK previously fed it, so a Pi outage or a
  rejected AppKey made every node rescan channels every other wake.
- Every frame a sleepy node unicasts in a wake is tracked
  (`mesh_inflight.h`). Unanswered frames go back to the RTC buffer and are
  resent next wake. REJECTED frames are dropped.
- The Pi authenticates before its replay check and re-ACKs an authenticated
  duplicate (same boot_count + seq) without republishing. Authenticating
  first also stops a forged header from moving a node's replay window.
- Limitation: the Pi's replay window is in memory, so a resend that crosses
  a Pi restart is republished once.
Plan: `docs/superpowers/plans/2026-09-24-mesh-ack-retry-and-link-counter.md`.
```

- [ ] **Step 2:** In `HANDOFF.md`, under "1. Delivery visibility" of the newest TL;DR, add a bullet:
  "*2026-09-24:* readings with no ACK are resent next wake (the Pi re-acks duplicates and authenticates before its replay check); the rescan counter is link-only again, so a Pi outage no longer triggers channel rescans."

- [ ] **Step 3 (controller, docx):** in §6.10 of the report, replace the sentence
  "Μια ανεπιβεβαίωτη μέτρηση ξαναμπαίνει στην ουρά με το ίδιο seq. Αν η «αποτυχία» ήταν απλώς χαμένο ACK, ο έλεγχος επανάληψης του Pi απορρίπτει το διπλότυπο."
  with:
  "Κάθε πλαίσιο που δεν πήρε απάντηση από το Pi ξαναμπαίνει στον buffer με τα ίδια ακριβώς bytes και ξαναστέλνεται στην επόμενη αφύπνιση. Αν είχε χαθεί μόνο το ACK, το Pi το αναγνωρίζει ως διπλότυπο (αφού πρώτα το αυθεντικοποιήσει), στέλνει ξανά ACK και δεν το ξαναδημοσιεύει. Ο μετρητής επανασάρωσης καναλιών μετρά μόνο αποτυχίες ζεύξης (L2), ώστε μια διακοπή του Pi να μην προκαλεί άσκοπες σαρώσεις."
  Re-render and check that the page count and chapter start pages did not move.

- [ ] **Step 4: Commit** (markdown files only; the controller commits the docx separately).

```bash
git add docs/superpowers/specs/2026-09-16-mesh-app-ack-design.md HANDOFF.md
git commit -m "docs: ACK revision 2026-09-24 (resend until acked, link-only rescan counter)"
```
