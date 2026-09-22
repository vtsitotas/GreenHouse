# CART Phase 2 (Synced-Wake Sleepy Relays) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Do not start Part C before Gate 0 (Task B4) has passed.** Parts A and B are
> useful and safe on today's Phase 1 fleet on their own; Part C is gated on
> real clock-drift measurements.

**Goal:** Let battery-powered (sleepy) field sensors relay for neighbours that cannot reach an always-on node, one sleepy hop deep, without any node staying awake.

**Architecture:** A sleepy relay (whose own parent is always-on) opens a short receptive window right after its sensor warm-up every cycle and forwards child frames cut-through to its always-on parent. Sleepy children schedule their wake from the *observed* window start of their parent (reactive re-anchoring every catch), size their listen window from observed dispersion (margin policy), and send after a random jitter with up to 3 attempts. All timing constants derive from the Phase 0 drift bench.

**Tech Stack:** Arduino/C++ on ESP32-C3 (`esp32:esp32` core 3.3.11, `--fqbn esp32:esp32:esp32c3`; WROOM variant `esp32:esp32:esp32`), host-compiled C++11 unit tests (`firmware/test/host`, `make` + `g++`), Python 3 on the Pi, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-cart-v2-revision.md` (read first — every number below is justified there) on top of `docs/superpowers/specs/2026-08-17-mesh-phase2-synced-wake-design.md`. Simulation: `docs/analysis/cart_sim.py`.

## Global Constraints

- Shipping cap: `MESH_SLEEPY_RELAY_DEPTH_MAX = 1`. `0` must reproduce Phase 1 behaviour exactly (rollback lever, Gate 1).
- Message lengths must stay unique: join 8, ACK 20, **beacon 29 (was 27)**, provision 33, data 61. The 61-byte sealed data packet does **not** change.
- Never set provisioning flag bit1 for a MAC whose join beacon did not advertise `caps ≥ 1` (spec §3.1 hazard: old firmware reads `flags == 1`).
- Planning constants until Gate 0 replaces them: relative bias `MESH_DRIFT_BIAS_PPM = 6000`, cycle `T = 300000 ms`, `MESH_WAKE_GUARD_MAX_MS = 4680`, `MESH_WAKE_GUARD_MIN_MS = 250`, jitter `J = 100 ms`, `3` attempts, `6` children max, `RX_OPEN` beacon every `100 ms`, knock `500 ms`.
- Every firmware task ends with `arduino-cli compile` of **all four sketches**: `edge_node_esp32_c3`, `fake_edge_node_esp32_c3`, `bridge_esp32` (all `esp32:esp32:esp32c3`) and `edge_node_esp32` (`esp32:esp32:esp32`). Every Pi task ends with `python -m pytest pi/tests/ -v` (the Windows-only `test_nodes.py::test_store_is_written_0600…` failure is pre-existing and unrelated).
- Beacon wire change = flag-day: the whole fleet (bridge included) is reflashed together at Gate 1.

---

## Part A — Prerequisites (safe on the Phase 1 fleet)

### Task A1: Always-on nodes answer an orphan at the trickle floor

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_config.h` (timing block)
- Modify: `firmware/libraries/GreenhouseMesh/mesh_node.h` (state block after `meshWindowDurationMs`; helper after `meshNoteNeighbor()`; `meshHandleBeacon()`)

**Interfaces:**
- Produces: `static bool meshNeighborHeardWithin(const uint8_t* mac, uint32_t now, uint32_t windowMs)`.

Fixes report §21.2 finding: today only a node's *first-ever* neighbour triggers `meshTrickleReset()`, so an orphan's 5 s discovery window usually meets a neighbour backed off to 60 s (≈ 8.3 % catch chance per wake).

- [ ] **Step 1: Add constants** — in `mesh_config.h`, directly after `#define MESH_RESCAN_AFTER_MS ...` (and its comment lines), add:

```c
#define MESH_ORPHAN_FRESH_MS         60000UL  // an UNROUTED beacon from a MAC not
                                              // heard for this long is a new orphan
#define MESH_ORPHAN_RESET_MIN_GAP_MS 10000UL  // at most one orphan-triggered
                                              // trickle reset per 10 s
```

- [ ] **Step 2: Add state + helper** — in `mesh_node.h`, after `static uint32_t meshWindowDurationMs  = MESH_WINDOW_DURATION_MS;` add:

```c
static uint32_t meshLastOrphanResetMs = 0;
```

and directly after the closing brace of `meshNoteNeighbor()` add:

```c
static bool meshNeighborHeardWithin(const uint8_t* mac, uint32_t now, uint32_t windowMs) {
  for (int i = 0; i < MESH_NEIGHBOR_SLOTS; i++) {
    if (meshNeighbors[i].used && meshMacEqual(meshNeighbors[i].mac, mac))
      return now - meshNeighbors[i].lastHeardMs < windowMs;
  }
  return false;
}
```

- [ ] **Step 3: Use it in `meshHandleBeacon()`** — replace

```c
  if (!meshNeighbors[0].used) meshTrickleReset();  // first neighbor seen
  meshNoteNeighbor(srcMac, now);
```

with

```c
  // Must be evaluated BEFORE meshNoteNeighbor() refreshes lastHeardMs.
  bool freshOrphan = b->rank == MESH_RANK_UNROUTED &&
                     !meshNeighborHeardWithin(srcMac, now, MESH_ORPHAN_FRESH_MS);
  if (!meshNeighbors[0].used) meshTrickleReset();  // first neighbor seen
  meshNoteNeighbor(srcMac, now);

  // A new orphan is inside a short discovery window right now (5 s for a
  // sleepy node) -- answer immediately at the trickle floor instead of
  // whatever backoff we drifted to (up to 60 s). Nettag-verified above, so
  // only network members can trigger it; rate-limited against a flapping node.
  if (freshOrphan && meshHasParent_ && !meshIsSelfSleepy() &&
      now - meshLastOrphanResetMs >= MESH_ORPHAN_RESET_MIN_GAP_MS) {
    meshLastOrphanResetMs = now;
    meshTrickleReset();
    meshSendBeaconNow(meshMyRank, meshBeaconIntervalMs);
    meshLastBeaconMs = now;
  }
```

- [ ] **Step 4: Compile all four sketches** (commands in Global Constraints). Expected: no errors.
- [ ] **Step 5: Bench check** — with an always-on relay stable for > 5 min (60 s trickle), power-cycle a sleepy node out of bridge range (`MESH_TEST_IGNORE_BRIDGE`) whose RTC hint is invalid. Expected on the relay's serial: an immediate beacon; on the sleepy node: `parent=...` within its 5 s window.
- [ ] **Step 6: Commit** — `git commit -m "fix(mesh): always-on nodes answer a new orphan at the trickle floor"` (+ attribution line).

### Task A2: Assign TTL at transmit time, not seal time

**Files:** Modify `firmware/libraries/GreenhouseMesh/mesh_node.h` (`meshFlushBuffer()`, `meshSendReading()`)

**Interfaces:** Produces `static uint8_t meshTxTtl()`.

TTL (header byte 15) is outside the GCM AAD and the nettag (`MESH_AAD_LEN = 15`), so rewriting it never breaks authentication. Today a buffered reading keeps the TTL of the rank it was sealed at and can expire if the node re-parents deeper (spec §3.6).

- [ ] **Step 1:** Directly above `static void meshBufferPush(...)` add:

```c
// TTL for a frame leaving NOW: current rank + margin, or the ceiling while
// unrouted. Byte 15 is outside both tags, so rewriting it is always safe.
static uint8_t meshTxTtl() {
  if (meshMyRank == MESH_RANK_UNROUTED) return MESH_MAX_TTL;
  uint16_t t = (uint16_t)meshMyRank + MESH_TTL_MARGIN;
  return t > MESH_MAX_TTL ? MESH_MAX_TTL : (uint8_t)t;
}
```

- [ ] **Step 2:** In `meshFlushBuffer()` replace

```c
    if (!meshUnicastToParent(meshBuf[meshBufHead])) break;
```

with

```c
    meshBuf[meshBufHead][15] = meshTxTtl();
    if (!meshUnicastToParent(meshBuf[meshBufHead])) break;
```

- [ ] **Step 3:** In `meshSendReading()` replace

```c
  uint8_t ttl = (meshMyRank == MESH_RANK_UNROUTED)
                    ? MESH_MAX_TTL
                    : (uint8_t)(meshMyRank + MESH_TTL_MARGIN);
```

with

```c
  uint8_t ttl = meshTxTtl();
```

- [ ] **Step 4:** Compile all four sketches. **Step 5:** Commit `fix(mesh): assign TTL at transmit time so buffered readings survive a deeper re-parent`.

### Task A3: Provisioning flags become a bit field (firmware first)

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_crypto.h` (`MESH_PROVISION_LEN` block, `meshOpenProvision()`)
- Modify: `firmware/libraries/GreenhouseMesh/mesh_store.h`
- Modify: `firmware/libraries/GreenhouseMesh/mesh_node.h` (`meshHandleProvision()`, add `meshIsSelfRelayCapable()`)
- Modify: `pi/shared/mesh_crypto.py` (`seal_provision`, `open_provision`)
- Test: `pi/tests/test_mesh_crypto.py`

**Interfaces:**
- Produces (firmware): `MESH_PROV_FLAG_SLEEPY 0x01`, `MESH_PROV_FLAG_LEAF_ONLY 0x02`, `meshStoreLeafOnly()`, `meshStoreSetLeafOnly(bool)`, `meshIsSelfRelayCapable()`.
- Produces (Pi): `seal_provision(app_key, mac, net_key, sleepy, leaf_only=False)`, `open_provision_flags(app_key, mac, blob) -> (net_key, sleepy, leaf_only)`; `open_provision` keeps its 2-tuple return.

Bit semantics are backward compatible for the values the Pi sends today (0 and 1). The Pi does not set bit1 until Task A4 adds the caps guard.

- [ ] **Step 1: Failing Pi tests** — append to `pi/tests/test_mesh_crypto.py`:

```python
def test_provision_flags_round_trip_with_leaf_only():
    blob = mc.seal_provision(APP, MAC, NET, sleepy=True, leaf_only=True)
    net, sleepy, leaf_only = mc.open_provision_flags(APP, MAC, blob)
    assert (net, sleepy, leaf_only) == (NET, True, True)


def test_default_blob_is_byte_identical_to_the_old_format():
    # Old firmware compares the flags byte with == 1, so a default blob must
    # still carry exactly 0x01 / 0x00.
    for sleepy in (True, False):
        blob = mc.seal_provision(APP, MAC, NET, sleepy=sleepy)
        _net, s, leaf = mc.open_provision_flags(APP, MAC, blob)
        assert (s, leaf) == (sleepy, False)
        assert mc.open_provision(APP, MAC, blob) == (NET, sleepy)
```

- [ ] **Step 2:** Run `python -m pytest pi/tests/test_mesh_crypto.py -v` → the two new tests FAIL (`TypeError` / `AttributeError`).

- [ ] **Step 3: Pi implementation** — in `pi/shared/mesh_crypto.py` replace `seal_provision` and `open_provision` with:

```python
PROV_FLAG_SLEEPY = 0x01
PROV_FLAG_LEAF_ONLY = 0x02


def seal_provision(app_key: bytes, mac: bytes, net_key: bytes, sleepy: bool,
                   leaf_only: bool = False) -> bytes:
    flags = (PROV_FLAG_SLEEPY if sleepy else 0) | (PROV_FLAG_LEAF_ONLY if leaf_only else 0)
    plain = net_key + bytes([flags])
    return AESGCM(app_key).encrypt(_provision_nonce(mac), plain, PROVISION_AAD)


def open_provision_flags(app_key: bytes, mac: bytes, blob: bytes) -> tuple:
    try:
        plain = AESGCM(app_key).decrypt(_provision_nonce(mac), blob, PROVISION_AAD)
    except (InvalidSignature, InvalidTag) as exc:
        raise MeshAuthError('provision blob failed authentication') from exc
    flags = plain[16]
    return plain[:16], bool(flags & PROV_FLAG_SLEEPY), bool(flags & PROV_FLAG_LEAF_ONLY)


def open_provision(app_key: bytes, mac: bytes, blob: bytes) -> tuple:
    net_key, sleepy, _leaf_only = open_provision_flags(app_key, mac, blob)
    return net_key, sleepy
```

- [ ] **Step 4:** Run the same pytest command → PASS (including the pre-existing provision tests).

- [ ] **Step 5: Firmware** — in `mesh_crypto.h`, below `#define MESH_PROVISION_LEN ...` add:

```c
#define MESH_PROV_FLAG_SLEEPY    0x01
#define MESH_PROV_FLAG_LEAF_ONLY 0x02   // enrolled as "never relay for others"
```

change the `meshOpenProvision` signature and its last two lines:

```c
static bool meshOpenProvision(const uint8_t* appKey, const uint8_t* mac,
                              const uint8_t* blob, int blobLen,
                              uint8_t* outNetKey16, bool* outSleepy, bool* outLeafOnly) {
```

```c
  memcpy(outNetKey16, plain, 16);
  *outSleepy   = (plain[16] & MESH_PROV_FLAG_SLEEPY) != 0;
  *outLeafOnly = (plain[16] & MESH_PROV_FLAG_LEAF_ONLY) != 0;
  return true;
```

In `mesh_store.h` add after `meshStoreSetSleepy`:

```c
// Absent key = relay-capable, so an already-enrolled fleet upgraded to CART
// firmware can relay without being re-enrolled.
static bool meshStoreLeafOnly()          { return meshPrefs.getBool("leafonly", false); }
static void meshStoreSetLeafOnly(bool v) { meshPrefs.putBool("leafonly", v); }
```

and add `meshPrefs.remove("leafonly");` inside `meshStoreClearProvisioning()`.

In `mesh_node.h` `meshHandleProvision()`: declare `bool leafOnly = false;` next to `bool sleepy = false;`, pass `&leafOnly` as the new last argument of `meshOpenProvision`, add `meshStoreSetLeafOnly(leafOnly);` after `meshStoreSetSleepy(sleepy);`, and change the log line to
`Serial.printf("[mesh] provisioned — sleepy=%d leafOnly=%d\n", sleepy ? 1 : 0, leafOnly ? 1 : 0);`.
Directly after `static bool meshIsSelfSleepy() { return meshStoreSleepy(); }` add:

```c
static bool meshIsSelfRelayCapable() { return !meshStoreLeafOnly(); }
```

- [ ] **Step 6:** Compile all four sketches. **Step 7:** Commit `feat(mesh): provisioning flags byte is a bit field (sleepy, leaf-only)`.

### Task A4: Capability-advertising join beacon + Pi caps guard

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_node.h` (`MESH_JOIN_MARKER` block, `meshSendJoinBeacon()`)
- Modify: `firmware/bridge_esp32/bridge_esp32.ino` (`onDataRecv` join branch)
- Modify: `pi/scripts/serial_bridge.py` (`new_state`, `handle_unenrolled`, main-loop publish)
- Modify: `pi/shared/nodes.py` (`Node`, `load`, `save`)
- Modify: `pi/portal/portal.py` (`queue_provision`, `api_nodes_add`, new `read_node_caps`)
- Test: `pi/tests/test_serial_bridge_mesh.py`, `pi/tests/test_nodes.py`, `pi/tests/test_portal_nodes.py`

**Interfaces:**
- Produces: join marker `MESH_JOIN_MARKER_CAPS 0x4B`; UART line `{"type":"unenrolled","mac":"…","caps":1}`; retained MQTT topic `greenhouse/node_caps` = JSON `{MAC: caps}`; `Node.leaf_only: bool = False`; `POST /api/nodes` accepts `"leaf_only": true` and answers **409** when the MAC's caps < 1.

- [ ] **Step 1: Failing tests.** Append to `pi/tests/test_serial_bridge_mesh.py`:

```python
def test_unenrolled_line_records_capabilities(state):
    sb.handle_unenrolled({'type': 'unenrolled', 'mac': MAC_S, 'caps': 1}, state)
    assert state['caps'][MAC_S] == 1


def test_unenrolled_line_without_caps_means_old_firmware(state):
    sb.handle_unenrolled({'type': 'unenrolled', 'mac': MAC_S}, state)
    assert state['caps'][MAC_S] == 0
```

Append to `pi/tests/test_nodes.py`:

```python
def test_leaf_only_round_trips_and_defaults_false(tmp_path):
    path = str(tmp_path / 'nodes.json')
    nodes.add(nodes.Node('206EF16C9DB0', bytes(16), 'zone2', 'a', True, leaf_only=True), path)
    nodes.add(nodes.Node('206EF16C6B50', bytes(16), 'zone3', 'b', True), path)
    store = nodes.load(path)
    assert store['206EF16C9DB0'].leaf_only is True
    assert store['206EF16C6B50'].leaf_only is False
```

Append to `pi/tests/test_portal_nodes.py` (uses that file's existing `client` fixture, `_auth()`, `MAC`, `KEY`):

```python
def test_leaf_only_refused_for_firmware_without_caps(client, monkeypatch):
    monkeypatch.setattr(portal, 'read_node_caps', lambda: {MAC: 0})
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': MAC, 'key': KEY, 'zone': 'zone2', 'leaf_only': True})
    assert r.status_code == 409
    assert MAC not in nodes.load(client._store)


def test_leaf_only_accepted_for_capable_firmware(client, monkeypatch):
    monkeypatch.setattr(portal, 'read_node_caps', lambda: {MAC: 1})
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': MAC, 'key': KEY, 'zone': 'zone2', 'leaf_only': True})
    assert r.status_code == 201
    assert nodes.load(client._store)[MAC].leaf_only is True
```

- [ ] **Step 2:** `python -m pytest pi/tests/ -v -k "caps or leaf_only"` → FAIL.

- [ ] **Step 3: `nodes.py`** — replace the dataclass and the two serialisers:

```python
@dataclass
class Node:
    mac: str
    app_key: bytes
    zone: str
    name: str
    sleepy: bool
    leaf_only: bool = False
```

in `load()`: `out[mac] = Node(mac, key, entry['zone'], entry['name'], bool(entry['sleepy']), bool(entry.get('leaf_only', False)))`;
in `save()`: add `'leaf_only': n.leaf_only` to each dict.

- [ ] **Step 4: `serial_bridge.py`** — add `'caps': {},` to `new_state()`'s dict; replace `handle_unenrolled` with:

```python
def handle_unenrolled(msg: dict, state: dict) -> None:
    mac = node_store.normalise_mac(msg['mac'])
    state['unenrolled'][mac] = time.time()
    # Missing key = pre-CART firmware (join marker 0x4A): must never be sent
    # a provisioning blob with bit1 set -- it would read "not sleepy".
    state['caps'][mac] = int(msg.get('caps', 0))
```

and in the main loop, next to the existing `client.publish('greenhouse/unenrolled', ...)`:

```python
            client.publish('greenhouse/node_caps', json.dumps(state['caps']), retain=True)
```

- [ ] **Step 5: `portal.py`** — add below `read_unenrolled()`:

```python
def read_node_caps() -> dict:
    import paho.mqtt.subscribe as subscribe
    try:
        msg = subscribe.simple("greenhouse/node_caps", hostname="127.0.0.1", msg_count=1, timeout=0.5)
        if msg and msg.payload:
            return json.loads(msg.payload.decode('utf-8'))
    except Exception:
        pass
    return {}
```

in `api_nodes_add`, replace the `node = Node(...)` statement with:

```python
    leaf_only = bool(body.get('leaf_only', False))
    if leaf_only and int(read_node_caps().get(mac, 0)) < 1:
        return jsonify({'error': 'sensor firmware too old for leaf-only; reflash first'}), 409
    node = Node(mac, key, zone, (body.get('name') or zone).strip(),
                bool(body.get('sleepy', True)), leaf_only)
```

and in `queue_provision` pass `node.leaf_only` as the new last argument of `mesh_crypto.seal_provision`.

- [ ] **Step 6:** Re-run the Step 2 command → PASS; then the full Pi suite.

- [ ] **Step 7: Firmware** — in `mesh_node.h` below `#define MESH_JOIN_MARKER 0x4A` add
`#define MESH_JOIN_MARKER_CAPS 0x4B   // same shape; sender understands provisioning bit1`
and in `meshSendJoinBeacon()` use `MESH_JOIN_MARKER_CAPS` instead of `MESH_JOIN_MARKER`.
In `bridge_esp32.ino` replace the join branch with:

```cpp
  if (len == (int)sizeof(MeshJoinBeacon) &&
      (data[1] == MESH_JOIN_MARKER || data[1] == MESH_JOIN_MARKER_CAPS)) {
    char mac[13]; meshFormatMac(data + 2, mac);
    uartPrintf("{\"type\":\"unenrolled\",\"mac\":\"%s\",\"caps\":%d}", mac,
               data[1] == MESH_JOIN_MARKER_CAPS ? 1 : 0);
    return;
  }
```

- [ ] **Step 8:** Compile all four sketches. **Step 9:** Commit `feat: sensors advertise provisioning capabilities; Pi refuses leaf-only for old firmware`.

### Task A5: ACK TTL follows the target's rank

**Files:** Modify `pi/scripts/serial_bridge.py` (`send_ack`, `handle_frame`), `firmware/libraries/GreenhouseMesh/mesh_node.h` (`meshBuildAck`), `firmware/bridge_esp32/bridge_esp32.ino` (`ack` branch). Test: `pi/tests/test_serial_bridge_mesh.py`.

**Interfaces:** `send_ack(ser, mac, seq, ok, ttl=None)`; `meshBuildAck(out, targetMac, seq, status, ttl)`; UART `ack` line gains optional `"ttl"`.

- [ ] **Step 1: Update the existing tests** in `test_serial_bridge_mesh.py` (`_frame()` seals `rank=1`, so ttl = 1 + 2 = 3):
  in `test_a_valid_frame_sends_a_positive_ack` expect `[((sentinel_ser, MAC_S, 1), {'ok': True, 'ttl': 3})]`; in `test_an_auth_failure_sends_a_negative_ack` expect `{'ok': False, 'ttl': 3}`. Add:

```python
def test_send_ack_carries_ttl_when_given():
    written = []
    sb.send_ack(type('S', (), {'write': lambda _s, b: written.append(b)})(), MAC_S, 7, True, ttl=4)
    assert b'"ttl":4' in written[0]
```

- [ ] **Step 2:** Run → the three tests FAIL.
- [ ] **Step 3:** In `serial_bridge.py`:

```python
ACK_TTL_MARGIN = 2   # mirrors MESH_TTL_MARGIN; spec 2026-09-23 §3.6
ACK_TTL_MAX = 16     # mirrors MESH_MAX_TTL


def send_ack(ser, mac: str, seq: int, ok: bool, ttl=None) -> None:
    payload = {'type': 'ack', 'mac': mac, 'seq': seq, 'ok': ok}
    if ttl is not None:
        payload['ttl'] = ttl
    _send_command(ser, payload)


def _ack_ttl(rank: int) -> int:
    return min(ACK_TTL_MAX, rank + ACK_TTL_MARGIN)
```

and change both `send_ack(ser, mac, header.seq, ok=…)` calls in `handle_frame` to add `ttl=_ack_ttl(header.rank)`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Firmware** — `meshBuildAck` gains `uint8_t ttl` as last parameter and sets `a.ttl = ttl;` (instead of `MESH_ACK_TTL`). In the bridge `ack` branch add after parsing `ok`:

```cpp
    const char* t = strstr(line, "\"ttl\":");
    int ttl = t ? atoi(t + 6) : MESH_ACK_TTL;
    if (ttl < 1) ttl = 1;
    if (ttl > MESH_MAX_TTL) ttl = MESH_MAX_TTL;
```

and call `meshBuildAck(ackPkt, mac, seq, ok ? MESH_ACK_OK : MESH_ACK_REJECTED, (uint8_t)ttl)`.
- [ ] **Step 6:** Compile all four sketches; full Pi suite. **Step 7:** Commit `feat(ack): ACK flood TTL = target rank + 2 instead of a fixed 6`.

---

## Part B — Phase 0 drift bench (Gate 0)

### Task B1: Bench build switch for the RTC slow-clock source

**Files:** Modify `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` (top includes, start of `setup()`).

API verified present in the installed core 3.3.11 (`esp_hw_support/port/esp32c3/include/soc/rtc.h`: `rtc_clk_8m_enable`, `rtc_clk_slow_src_set`, `rtc_clk_cal`, `RTC_CAL_RTC_MUX`; `esp_private/esp_clk.h`: `esp_clk_slowclk_cal_set`; `soc/clk_tree_defs.h`: `SOC_RTC_SLOW_CLK_SRC_RC_FAST_D256`).

- [ ] **Step 1:** Below `#include "node_key.h"` add:

```cpp
#ifdef MESH_BENCH_RTC_FAST
#include "soc/rtc.h"
#include "esp_private/esp_clk.h"
#endif
```

and as the first statements of `setup()` after `Serial.begin(115200);`:

```cpp
#ifdef MESH_BENCH_RTC_FAST
  // Bench-only (Gate 0 A/B): run RTC_SLOW_CLK from the ~17.5 MHz RC / 256
  // instead of the default ~136 kHz RC. Re-applied every boot: the
  // bootloader restores the default source on each deep-sleep wake.
  rtc_clk_8m_enable(true, true);
  rtc_clk_slow_src_set(SOC_RTC_SLOW_CLK_SRC_RC_FAST_D256);
  esp_clk_slowclk_cal_set(rtc_clk_cal(RTC_CAL_RTC_MUX, 1024));
#endif
```

- [ ] **Step 2:** Compile both ways:
  `arduino-cli compile --fqbn esp32:esp32:esp32c3 firmware/edge_node_esp32_c3` and
  `arduino-cli compile --fqbn esp32:esp32:esp32c3 --build-property "compiler.cpp.extra_flags=-DMESH_BENCH_RTC_FAST" firmware/edge_node_esp32_c3`. Expected: both compile.
- [ ] **Step 3:** Commit `bench: optional RC_FAST_D256 RTC slow clock for the Gate 0 drift A/B`.

### Task B2: Drift logger on the Pi

**Files:** Create `pi/tools/drift_logger.py`; Test `pi/tests/test_drift_logger.py`.

**Interfaces:** `format_line(topic: str, retain: bool, now: float) -> str | None` (CSV `mac,arrival_epoch_s`), CLI `python pi/tools/drift_logger.py --out drift.csv`.

- [ ] **Step 1: Failing test:**

```python
# pi/tests/test_drift_logger.py
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import drift_logger as dl


def test_live_mesh_message_becomes_a_csv_line():
    assert dl.format_line('greenhouse/nodes/206EF16C9DB0/mesh', False, 1700000000.123456) \
        == '206EF16C9DB0,1700000000.123456'


def test_retained_replay_is_ignored():
    assert dl.format_line('greenhouse/nodes/206EF16C9DB0/mesh', True, 1.0) is None


def test_other_topics_are_ignored():
    assert dl.format_line('greenhouse/nodes/206EF16C9DB0/status', False, 1.0) is None
```

- [ ] **Step 2:** `python -m pytest pi/tests/test_drift_logger.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement:**

```python
#!/usr/bin/env python3
"""Bench-only: log the Pi-side arrival time of every LIVE /mesh message.

The Pi clock (NTP-disciplined) is the crystal reference; arrival spacing of a
sleepy node's reports measures that node's RC-clock cycle length. Retained
replays carry no timing information and are skipped.
"""
import argparse
import sys
import time

import paho.mqtt.client as mqtt


def format_line(topic: str, retain: bool, now: float):
    parts = topic.split('/')
    if retain or len(parts) != 4 or parts[0] != 'greenhouse' or parts[1] != 'nodes' \
            or parts[3] != 'mesh':
        return None
    return f'{parts[2]},{now:.6f}'


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--host', default='127.0.0.1')
    args = ap.parse_args(argv)
    out = open(args.out, 'a', buffering=1)

    def on_message(_c, _u, msg):
        line = format_line(msg.topic, msg.retain, time.time())
        if line:
            out.write(line + '\n')

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='drift-logger')
    client.on_message = on_message
    client.connect(args.host, 1883, 30)
    client.subscribe('greenhouse/nodes/+/mesh', qos=1)
    client.loop_forever()


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4:** Test → PASS. **Step 5:** Commit `bench: drift logger (arrival times of live /mesh reports)`.

### Task B3: Drift analysis → simulator inputs

**Files:** Create `pi/tools/drift_analyze.py`; Test `pi/tests/test_drift_analyze.py`.

**Interfaces:** `per_node_rates(times: list[float], period_s: float) -> list[tuple[float, float]]` (end time, rate error of each clean single cycle); `summarize(rates) -> dict(bias, step, n)`; `pair_relative(rates_a, rates_b, period_s) -> dict(bias, step, n)`; CLI prints JSON with `worst.bias` / `worst.step` to paste into `docs/analysis/cart_sim.py` `MEASURED`.

Method (spec §2): only intervals that are one clean cycle (`round(Δ/T) == 1`) count; `rate = Δ/T − 1`; `bias = mean(rate)`; `step = std(rate_i − rate_{i−1})` over consecutive clean cycles — an upper bound (it includes delivery jitter), which is the safe side for sizing guard windows. Pairwise relative drift = same statistics on `rate_child − rate_relay` for cycles ending within `T/2` of each other. Phase 1 sleepy nodes run `T + boot latency`; the constant term is identical for both nodes of a pair and cancels in the relative figures.

- [ ] **Step 1: Failing tests:**

```python
# pi/tests/test_drift_analyze.py
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import drift_analyze as da


def test_constant_fast_clock_gives_its_bias_and_zero_step():
    times = [i * 300.0 * (1 - 0.006) for i in range(50)]      # runs 0.6 % fast
    rates = da.per_node_rates(times, 300.0)
    s = da.summarize(rates)
    assert s['bias'] == pytest.approx(-0.006, abs=1e-9)
    assert s['step'] == pytest.approx(0.0, abs=1e-9)
    assert s['n'] == 49


def test_a_missed_report_is_not_counted_as_a_long_cycle():
    times = [0.0, 300.0, 900.0, 1200.0]          # one report missing
    rates = da.per_node_rates(times, 300.0)
    assert len(rates) == 2


def test_pair_relative_bias_is_the_difference():
    a = da.per_node_rates([i * 300.0 * 1.001 for i in range(20)], 300.0)
    b = da.per_node_rates([i * 300.0 * 1.003 for i in range(20)], 300.0)
    rel = da.pair_relative(a, b, 300.0)
    assert rel['bias'] == pytest.approx(0.002, abs=1e-6)
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement:**

```python
#!/usr/bin/env python3
"""Bench-only: turn drift_logger CSVs into CART simulator inputs (Gate 0)."""
import argparse
import csv
import itertools
import json
import statistics
import sys
from collections import defaultdict


def per_node_rates(times, period_s):
    times = sorted(times)
    out = []
    for prev, cur in zip(times, times[1:]):
        delta = cur - prev
        if round(delta / period_s) == 1:
            out.append((cur, delta / period_s - 1.0))
    return out


def _stats(values):
    if len(values) < 2:
        return {'bias': 0.0, 'step': 0.0, 'n': len(values)}
    diffs = [b - a for a, b in zip(values, values[1:])]
    return {'bias': statistics.mean(values),
            'step': statistics.pstdev(diffs) if len(diffs) > 1 else 0.0,
            'n': len(values)}


def summarize(rates):
    return _stats([r for _t, r in rates])


def pair_relative(rates_a, rates_b, period_s):
    rel = []
    for t_b, r_b in rates_b:
        best = min(rates_a, key=lambda x: abs(x[0] - t_b), default=None)
        if best is not None and abs(best[0] - t_b) <= period_s / 2:
            rel.append(r_b - best[1])
    return _stats(rel)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('csv')
    ap.add_argument('--period', type=float, required=True, help='nominal T in seconds')
    args = ap.parse_args(argv)
    times = defaultdict(list)
    with open(args.csv) as fh:
        for mac, t in csv.reader(fh):
            times[mac].append(float(t))
    rates = {mac: per_node_rates(ts, args.period) for mac, ts in times.items()}
    pairs = {f'{a}->{b}': pair_relative(rates[a], rates[b], args.period)
             for a, b in itertools.permutations(sorted(rates), 2)}
    worst_bias = max((abs(p['bias']) for p in pairs.values()), default=0.0)
    worst_step = max((p['step'] for p in pairs.values()), default=0.0)
    print(json.dumps({'period_s': args.period,
                      'per_node': {m: summarize(r) for m, r in rates.items()},
                      'pairs': pairs,
                      'worst': {'bias': worst_bias, 'step': worst_step}}, indent=2))


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4:** Test → PASS; full Pi suite. **Step 5:** Commit `bench: drift analysis (per-node and pairwise relative) for Gate 0`.

### Task B4: Run the bench and decide Gate 0 (procedure, no new code)

- [ ] **Step 1:** Set `MESH_SLEEP_INTERVAL_MS` to `300000UL` in `mesh_config.h` **on a bench branch only**. Flash zone2/3/4 with the default build, enrolled `sleepy=true`, co-located in the greenhouse (same thermal environment), all in direct bridge range.
- [ ] **Step 2:** On the Pi: `python3 pi/tools/drift_logger.py --out /home/pi/drift_rc.csv` for ≥ 24 h spanning a day/night cycle.
- [ ] **Step 3:** Reflash the same boards with `-DMESH_BENCH_RTC_FAST` (Task B1 Step 2 command, then upload), log another ≥ 24 h to `drift_fast.csv`.
- [ ] **Step 4:** `python3 pi/tools/drift_analyze.py /home/pi/drift_rc.csv --period 300` and the same for `drift_fast.csv`. Record both JSON outputs in the spec §5 (append a "Gate 0 results" subsection with date and raw numbers).
- [ ] **Step 5:** For each clock source, put `worst.bias` into `MEASURED["bias"]` and `worst.step` into `MEASURED["step_300"]` (and `3 × step` into `step_900`) in `docs/analysis/cart_sim.py`; run `python docs/analysis/cart_sim.py`.
- [ ] **Step 6: Decide** with the spec §6 Gate 0 rule, using the "margin" rows of section A2 for `G_max = 2|b|T·1.3`:
  - PASS (136 kHz RC) → keep default clock.
  - FAIL RC, PASS RC_FAST_D256 → Part C adds the Task B1 clock switch to production builds (+0.12 mAh/day).
  - FAIL both → **stop**: CART not viable without an external crystal; record in spec and HANDOFF, keep Phase 1.
  - Choose T: the largest of {300 s, 900 s} meeting miss ≤ 1 % and sweeps ≤ 12/year; if 900 s, also raise the relay backstop per spec §3.7.
- [ ] **Step 7:** Commit the recorded results: `docs: CART Gate 0 drift results and decision`.

---

## Part C — CART core (only after Gate 0 PASS)

Constants below are the planning values; **replace them with the Gate 0 values** (Task B4 Step 6) where marked `/* Gate 0 */`.

### Task C1: Pure scheduling logic `mesh_sched.h` with host tests

**Files:**
- Create: `firmware/libraries/GreenhouseMesh/mesh_sched.h`
- Create: `firmware/test/host/sched_tests.cpp`
- Modify: `firmware/test/host/Makefile`
- Create: `pi/tests/test_firmware_sched.py`

**Interfaces (produced, used by C3/C4):**

```c
typedef struct { uint16_t gMinMs, gMaxMs, padMs, jitterMs, perChildMs, knockMs, ackLingerMs;
                 uint8_t attempts, kNum, kDen; uint32_t baseAwakeMs; } MeshSchedCfg;
typedef struct { int32_t deltaMs; uint16_t guardMs; uint16_t hist[16];
                 uint8_t histN, histHead, missRun, estKnown, anchored; } MeshSchedState;
typedef enum { MESH_SCHED_RETRY = 0, MESH_SCHED_EXTENDED = 1, MESH_SCHED_SWEEP = 2 } MeshSchedAction;
void     meshSchedReset(MeshSchedState*, const MeshSchedCfg*);
void     meshSchedOnAnchor(MeshSchedState*, const MeshSchedCfg*);          // sweep catch
void     meshSchedOnCatch(MeshSchedState*, const MeshSchedCfg*, int32_t errMs);
MeshSchedAction meshSchedOnMiss(MeshSchedState*, const MeshSchedCfg*);
uint32_t meshSchedPredictedCatchMs(const MeshSchedState*, uint32_t leadMs);
uint32_t meshSchedSleepMs(const MeshSchedState*, uint32_t anchorMs, uint32_t nowMs,
                          uint32_t cycleMs, uint32_t leadMs, uint32_t minSleepMs);
uint16_t meshSchedGuardMax(uint32_t cycleMs, uint32_t biasPpm);
uint32_t meshSchedServiceWindowMs(const MeshSchedCfg*, uint16_t maxChildGuardMs, uint8_t nChildren);
uint32_t meshSchedRelayBackstopMs(const MeshSchedCfg*, uint8_t maxChildren);
uint16_t meshSchedJitterMs(const MeshSchedCfg*, uint32_t rnd);
```

`errMs` = observed catch time (ms since this boot) − `meshSchedPredictedCatchMs(state, leadMs)` computed **before** the update.

- [ ] **Step 1: Write the failing host tests** `firmware/test/host/sched_tests.cpp`:

```cpp
// firmware/test/host/sched_tests.cpp -- host unit tests for mesh_sched.h
#include <cstdio>
#include "../../libraries/GreenhouseMesh/mesh_sched.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static MeshSchedCfg cfg() {
  MeshSchedCfg c;
  c.gMinMs = 250; c.gMaxMs = 4680; c.padMs = 50; c.jitterMs = 100; c.perChildMs = 150;
  c.knockMs = 500; c.ackLingerMs = 300; c.attempts = 3; c.kNum = 3; c.kDen = 2;
  c.baseAwakeMs = 2500;
  return c;
}

int main() {
  MeshSchedCfg c = cfg();
  MeshSchedState s;

  meshSchedReset(&s, &c);
  CHECK(s.anchored == 0 && s.estKnown == 0 && s.guardMs == 4680);

  meshSchedOnAnchor(&s, &c);
  CHECK(s.anchored == 1 && s.estKnown == 0 && s.deltaMs == 0 && s.guardMs == 4680);

  // First catch after an anchor measures the per-cycle offset outright and
  // does NOT enter the dispersion history (it is bias, not dispersion).
  meshSchedOnCatch(&s, &c, 1800);
  CHECK(s.estKnown == 1 && s.deltaMs == 1800 && s.histN == 0 && s.guardMs == 4680);

  // Later catches: EWMA (0.3) and dispersion history; G fixed until 16 entries.
  meshSchedOnCatch(&s, &c, 100);
  CHECK(s.deltaMs == 1830 && s.histN == 1 && s.guardMs == 4680);
  for (int i = 0; i < 15; i++) meshSchedOnCatch(&s, &c, (i % 2) ? 400 : -400);
  // G = 2 * 1.5 * max|err| + pad = 2*1.5*400 + 50 = 1250
  CHECK(s.histN == 16 && s.guardMs == 1250);

  // Tight clocks clamp to G_min.
  for (int i = 0; i < 16; i++) meshSchedOnCatch(&s, &c, 20);
  CHECK(s.guardMs == 250);

  // Misses: double, then G_max (extended), then sweep.
  CHECK(meshSchedOnMiss(&s, &c) == MESH_SCHED_RETRY && s.guardMs == 500 && s.missRun == 1);
  CHECK(meshSchedOnMiss(&s, &c) == MESH_SCHED_EXTENDED && s.guardMs == 4680 && s.missRun == 2);
  CHECK(meshSchedOnMiss(&s, &c) == MESH_SCHED_SWEEP && s.anchored == 0);

  // A catch clears the miss run.
  meshSchedReset(&s, &c); meshSchedOnAnchor(&s, &c); meshSchedOnCatch(&s, &c, 0);
  meshSchedOnMiss(&s, &c); meshSchedOnCatch(&s, &c, 10);
  CHECK(s.missRun == 0);

  // Predicted catch = lead + G/2.
  meshSchedReset(&s, &c); meshSchedOnAnchor(&s, &c);
  CHECK(meshSchedPredictedCatchMs(&s, 2000) == 2000 + 2340);

  // Sleep: wake at anchor + T + delta - G/2 - lead, measured from now.
  s.deltaMs = 120; s.guardMs = 1000;
  CHECK(meshSchedSleepMs(&s, 2600, 3100, 300000, 2000, 1000) == 2600 + 300000 + 120 - 500 - 2000 - 3100);
  CHECK(meshSchedSleepMs(&s, 0, 400000, 300000, 2000, 1000) == 1000);   // clamped

  CHECK(meshSchedGuardMax(300000, 6000) == 4680);    // 2 * 0.006 * 300 s * 1.3
  CHECK(meshSchedGuardMax(900000, 6000) == 14040);

  // S = G_child/2 + attempts*J + n*perChild + knock
  CHECK(meshSchedServiceWindowMs(&c, 1000, 4) == 500 + 300 + 600 + 500);
  // backstop = base + G_max/2 + attempts*J + max*perChild + knock + linger
  CHECK(meshSchedRelayBackstopMs(&c, 6) == 2500 + 2340 + 300 + 900 + 500 + 300);

  for (uint32_t r = 0; r < 1000; r += 37) CHECK(meshSchedJitterMs(&c, r * 2654435761u) < 100);

  if (failures) { printf("%d FAILED\n", failures); return 1; }
  printf("ALL PASS\n");
  return 0;
}
```

- [ ] **Step 2: Makefile** — replace `firmware/test/host/Makefile` with:

```make
# firmware/test/host/Makefile
CXXFLAGS = -std=c++11 -Wall -Wextra -Werror -O1

all: layout_vectors sched_tests

layout_vectors: layout_vectors.cpp ../../libraries/GreenhouseMesh/mesh_packet.h
	$(CXX) $(CXXFLAGS) -o $@ layout_vectors.cpp

sched_tests: sched_tests.cpp ../../libraries/GreenhouseMesh/mesh_sched.h
	$(CXX) $(CXXFLAGS) -o $@ sched_tests.cpp

.PHONY: all clean
clean:
	rm -f layout_vectors sched_tests
```

- [ ] **Step 3: pytest wrapper** `pi/tests/test_firmware_sched.py`:

```python
# pi/tests/test_firmware_sched.py
import os
import subprocess

import pytest

HOST_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'firmware', 'test', 'host')


def test_mesh_sched_host_unit_tests():
    try:
        rc = subprocess.call(['g++', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        rc = 1
    if rc != 0:
        pytest.skip('g++ not available')
    subprocess.check_call(['make', '-s', 'sched_tests'], cwd=HOST_DIR)
    out = subprocess.run([os.path.join(HOST_DIR, 'sched_tests')], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout
    assert 'ALL PASS' in out.stdout
```

- [ ] **Step 4:** `python -m pytest pi/tests/test_firmware_sched.py -v` → FAIL (header missing) where g++ exists.

- [ ] **Step 5: Implement** `firmware/libraries/GreenhouseMesh/mesh_sched.h`:

```c
// firmware/libraries/GreenhouseMesh/mesh_sched.h
#pragma once
// CART schedule arithmetic -- pure, no Arduino/ESP-IDF includes, so it is
// unit-tested on a host (firmware/test/host/sched_tests.cpp). Every rule here
// is justified in docs/superpowers/specs/2026-09-23-cart-v2-revision.md §3.
#include <stdint.h>

#define MESH_SCHED_HIST 16

typedef struct {
  uint16_t gMinMs, gMaxMs, padMs, jitterMs, perChildMs, knockMs, ackLingerMs;
  uint8_t  attempts, kNum, kDen;
  uint32_t baseAwakeMs;
} MeshSchedCfg;

typedef struct {
  int32_t  deltaMs;                 // learned per-cycle offset (parent period in
                                    // our clock - T), incl. constant wake latency
  uint16_t guardMs;                 // current listen window G
  uint16_t hist[MESH_SCHED_HIST];   // |err| of recent catches
  uint8_t  histN, histHead, missRun, estKnown, anchored;
} MeshSchedState;

typedef enum { MESH_SCHED_RETRY = 0, MESH_SCHED_EXTENDED = 1, MESH_SCHED_SWEEP = 2 } MeshSchedAction;

static inline void meshSchedReset(MeshSchedState* s, const MeshSchedCfg* c) {
  s->deltaMs = 0; s->guardMs = c->gMaxMs; s->histN = 0; s->histHead = 0;
  s->missRun = 0; s->estKnown = 0; s->anchored = 0;
}

// A sweep caught the parent: timing re-anchored, per-cycle offset unknown.
static inline void meshSchedOnAnchor(MeshSchedState* s, const MeshSchedCfg* c) {
  meshSchedReset(s, c);
  s->anchored = 1;
}

static inline uint16_t meshSchedAbs16(int32_t v) {
  uint32_t a = (uint32_t)(v < 0 ? -v : v);
  return a > 0xFFFF ? 0xFFFF : (uint16_t)a;
}

static inline void meshSchedOnCatch(MeshSchedState* s, const MeshSchedCfg* c, int32_t errMs) {
  s->missRun = 0;
  if (!s->estKnown) {                // first period observed since the anchor
    s->deltaMs += errMs;
    s->estKnown = 1;
    return;
  }
  s->deltaMs += (errMs * 3) / 10;    // EWMA, gain 0.3 (cart_sim.py)
  s->hist[s->histHead] = meshSchedAbs16(errMs);
  s->histHead = (uint8_t)((s->histHead + 1) % MESH_SCHED_HIST);
  if (s->histN < MESH_SCHED_HIST) s->histN++;
  if (s->histN == MESH_SCHED_HIST) {
    uint32_t mx = 0;
    for (int i = 0; i < MESH_SCHED_HIST; i++) if ((uint32_t)s->hist[i] > mx) mx = s->hist[i];
    uint32_t g = 2u * mx * (uint32_t)c->kNum / (uint32_t)c->kDen + (uint32_t)c->padMs;
    if (g < (uint32_t)c->gMinMs) g = c->gMinMs;
    if (g > (uint32_t)c->gMaxMs) g = c->gMaxMs;
    s->guardMs = (uint16_t)g;
  }
}

static inline MeshSchedAction meshSchedOnMiss(MeshSchedState* s, const MeshSchedCfg* c) {
  s->missRun++;
  if (s->missRun >= 3) { s->anchored = 0; return MESH_SCHED_SWEEP; }
  if (s->missRun == 2) { s->guardMs = c->gMaxMs; return MESH_SCHED_EXTENDED; }
  uint32_t g = (uint32_t)s->guardMs * 2u;
  s->guardMs = (uint16_t)(g > (uint32_t)c->gMaxMs ? (uint32_t)c->gMaxMs : g);
  return MESH_SCHED_RETRY;
}

static inline uint32_t meshSchedPredictedCatchMs(const MeshSchedState* s, uint32_t leadMs) {
  return leadMs + s->guardMs / 2;
}

static inline uint32_t meshSchedSleepMs(const MeshSchedState* s, uint32_t anchorMs, uint32_t nowMs,
                                        uint32_t cycleMs, uint32_t leadMs, uint32_t minSleepMs) {
  int64_t target = (int64_t)anchorMs + cycleMs + s->deltaMs - s->guardMs / 2 - leadMs;
  int64_t sleep = target - (int64_t)nowMs;
  return sleep < (int64_t)minSleepMs ? minSleepMs : (uint32_t)sleep;
}

static inline uint16_t meshSchedGuardMax(uint32_t cycleMs, uint32_t biasPpm) {
  uint64_t g = (uint64_t)2 * cycleMs * biasPpm * 13 / 10 / 1000000u;
  return g > 0xFFFF ? 0xFFFF : (uint16_t)g;
}

static inline uint32_t meshSchedServiceWindowMs(const MeshSchedCfg* c, uint16_t maxChildGuardMs,
                                                uint8_t nChildren) {
  return maxChildGuardMs / 2 + (uint32_t)c->attempts * c->jitterMs +
         (uint32_t)nChildren * c->perChildMs + c->knockMs;
}

static inline uint32_t meshSchedRelayBackstopMs(const MeshSchedCfg* c, uint8_t maxChildren) {
  return c->baseAwakeMs + c->gMaxMs / 2 + (uint32_t)c->attempts * c->jitterMs +
         (uint32_t)maxChildren * c->perChildMs + c->knockMs + c->ackLingerMs;
}

static inline uint16_t meshSchedJitterMs(const MeshSchedCfg* c, uint32_t rnd) {
  return c->jitterMs ? (uint16_t)(rnd % c->jitterMs) : 0;
}
```

- [ ] **Step 6:** Test → PASS (`ALL PASS`). Also `arduino-cli compile` all four sketches (header not yet included anywhere — must still compile).
- [ ] **Step 7:** Commit `feat(cart): host-tested schedule arithmetic (margin guard policy, bias-derived G_max)`.

### Task C2: Beacon v3, sleepy-parent acceptance, relay state

**Files:** Modify `firmware/libraries/GreenhouseMesh/mesh_config.h`, `firmware/libraries/GreenhouseMesh/mesh_node.h`.

**Interfaces (produced):** `MESH_FLAG_RX_OPEN 0x02`, `MESH_FLAG_RELAY_CAP 0x04`; `MeshBeacon` 29 bytes (`sleepy_depth`, `guard_hint_q` before `tag`); `meshSendBeaconEx(rank, intervalMs, extraFlags, rxElapsedMs)`; state `meshParentSleepy`, `meshMySleepyDepth`, `meshMyGuardHintQ`, `meshRxWindowOpen`, `meshRxOpenedMs`; child registry `meshCartNoteChild()`, `meshCartChildCount()`, `meshCartMaxChildGuardMs()`, `meshCartAgeChildren()`; catch signal `meshCatchSeen`, `meshCatchAnchorMs`; `meshCanServeAsSleepyParent()`; `meshCycleMs()`.

- [ ] **Step 1: Config** — add to `mesh_config.h` after the deep-sleep block:

```c
// ── CART Phase 2 (spec 2026-09-23-cart-v2-revision) ──────────────────────────
#define MESH_SLEEPY_RELAY_DEPTH_MAX  1       // 0 = exactly Phase 1 (rollback)
#define MESH_FLAG_RX_OPEN            0x02    // beacon: relay's receptive window open
#define MESH_FLAG_RELAY_CAP          0x04    // beacon: sleepy sender accepts children
#define MESH_MAX_SLEEPY_CHILDREN     6
#define MESH_CHILD_TTL_CYCLES        3
#define MESH_RX_BEACON_PERIOD_MS     100UL
#define MESH_DRIFT_BIAS_PPM          6000UL  /* Gate 0 */
#define MESH_WAKE_GUARD_MIN_MS       250
#define MESH_WAKE_GUARD_MAX_MS       4680    /* Gate 0: 2*b*T*1.3 */
#define MESH_CART_JITTER_MS          100
#define MESH_CART_ATTEMPTS           3
#define MESH_CART_PER_CHILD_MS       150
#define MESH_KNOCK_WINDOW_MS         500
#define MESH_RELAY_ACK_LINGER_MS     300
#define MESH_CYCLE_MIN_MS            30000UL
#define MESH_CYCLE_MAX_MS            3600000UL
static_assert((uint64_t)MESH_WAKE_GUARD_MAX_MS * 1000000ULL >=
              2ULL * MESH_SLEEP_INTERVAL_MS * MESH_DRIFT_BIAS_PPM,
              "G_max below 2*bias*T: a re-anchored child can never catch its parent (spec §3.3)");
```

- [ ] **Step 2: Beacon struct** — in `mesh_node.h` replace the `MeshBeacon` struct with:

```c
typedef struct __attribute__((packed)) {
  uint8_t  magic;               // MESH_MAGIC_V2 (0x48)
  uint8_t  mac[6];
  uint8_t  rank;                // 255 = unrouted
  uint16_t seq;
  uint32_t beacon_interval_ms;  // gap to sender's next beacon; for a sleepy
                                // sender this is its cycle period T
  uint32_t window_duration_ms;  // v3: ms since the sender's receptive window
                                // opened (valid only with MESH_FLAG_RX_OPEN)
  uint8_t  flags;               // bit0 SLEEPY, bit1 RX_OPEN, bit2 RELAY_CAP
  uint8_t  sleepy_depth;        // v3: consecutive sleepy ancestors (0 = parent always-on)
  uint8_t  guard_hint_q;        // v3: sender's current guard window, 250 ms units
  uint8_t  tag[MESH_NETTAG_LEN];
} MeshBeacon;                   // 29 bytes
```

- [ ] **Step 3: State** — after `static uint32_t meshLastOrphanResetMs = 0;` add:

```c
static bool     meshParentSleepy   = false;
static uint8_t  meshMySleepyDepth  = 0;
static uint8_t  meshMyGuardHintQ   = 0;
static volatile bool     meshRxWindowOpen = false;
static uint32_t          meshRxOpenedMs   = 0;
static volatile bool     meshCatchSeen    = false;
static volatile uint32_t meshCatchAnchorMs = 0;

typedef struct { uint8_t mac[6]; uint8_t guardQ; uint8_t silent; uint8_t heard; uint8_t used; } MeshChild;
RTC_DATA_ATTR static MeshChild meshChildren[MESH_MAX_SLEEPY_CHILDREN];
```

- [ ] **Step 4: Registry + role helpers** — directly after `static bool meshIsSelfRelayCapable() ...` (added in Task A3; it must come after `meshIsSelfSleepy()`, which these helpers call) add:

```c
static uint8_t meshCartChildCount() {
  uint8_t n = 0;
  for (int i = 0; i < MESH_MAX_SLEEPY_CHILDREN; i++) n += meshChildren[i].used ? 1 : 0;
  return n;
}

static void meshCartNoteChild(const uint8_t* mac, uint8_t guardQ) {
  int freeSlot = -1;
  for (int i = 0; i < MESH_MAX_SLEEPY_CHILDREN; i++) {
    if (meshChildren[i].used && meshMacEqual(meshChildren[i].mac, mac)) {
      if (guardQ) meshChildren[i].guardQ = guardQ;
      meshChildren[i].heard = 1;
      meshChildren[i].silent = 0;
      return;
    }
    if (!meshChildren[i].used && freeSlot < 0) freeSlot = i;
  }
  if (freeSlot < 0) return;               // full: admission control via RELAY_CAP
  memcpy(meshChildren[freeSlot].mac, mac, 6);
  meshChildren[freeSlot].guardQ = guardQ;
  meshChildren[freeSlot].heard = 1;
  meshChildren[freeSlot].silent = 0;
  meshChildren[freeSlot].used = 1;
}

static uint16_t meshCartMaxChildGuardMs() {
  uint16_t mx = MESH_WAKE_GUARD_MAX_MS;   // unknown/new child: assume the ceiling
  bool any = false;
  for (int i = 0; i < MESH_MAX_SLEEPY_CHILDREN; i++) {
    if (!meshChildren[i].used || !meshChildren[i].guardQ) continue;
    uint16_t g = (uint16_t)meshChildren[i].guardQ * 250;
    if (!any || g > mx) { mx = g; any = true; }
  }
  return mx;
}

static void meshCartAgeChildren() {
  for (int i = 0; i < MESH_MAX_SLEEPY_CHILDREN; i++) {
    if (!meshChildren[i].used) continue;
    if (!meshChildren[i].heard && ++meshChildren[i].silent >= MESH_CHILD_TTL_CYCLES)
      meshChildren[i].used = 0;
    meshChildren[i].heard = 0;
  }
}

static bool meshCanServeAsSleepyParent() {
  return meshIsSelfSleepy() && meshIsSelfRelayCapable() && meshHasParent_ &&
         meshMySleepyDepth < MESH_SLEEPY_RELAY_DEPTH_MAX &&
         meshCartChildCount() < MESH_MAX_SLEEPY_CHILDREN;
}

// A sleepy child follows its sleepy parent's advertised period (spec §4:
// a 60 s test build and a 300 s production build must still rendezvous).
static uint32_t meshCycleMs() {
  if (meshHasParent_ && meshParentSleepy &&
      meshParentIntervalMs >= MESH_CYCLE_MIN_MS && meshParentIntervalMs <= MESH_CYCLE_MAX_MS)
    return meshParentIntervalMs;
  return MESH_SLEEP_INTERVAL_MS;
}
```

- [ ] **Step 5: Beacon TX** — replace `meshSendBeaconNow()` with:

```c
static void meshSendBeaconEx(uint8_t rank, uint32_t advertisedIntervalMs,
                             uint8_t extraFlags, uint32_t rxElapsedMs) {
  if (!meshLoadKeys()) return;
  MeshBeacon b;
  b.magic              = MESH_MAGIC_V2;
  memcpy(b.mac, meshSelfMac, 6);
  b.rank               = rank;
  b.seq                = meshBeaconSeq++;
  b.beacon_interval_ms = advertisedIntervalMs;
  b.window_duration_ms = (extraFlags & MESH_FLAG_RX_OPEN) ? rxElapsedMs : 0;
  b.flags              = (meshIsSelfSleepy() ? MESH_FLAG_SLEEPY : 0) | extraFlags;
  if (meshIsSelfSleepy() && meshCanServeAsSleepyParent()) b.flags |= MESH_FLAG_RELAY_CAP;
  b.sleepy_depth       = meshMySleepyDepth;
  b.guard_hint_q       = meshMyGuardHintQ;
  meshCmacTruncated(meshNetKey, (const uint8_t*)&b, sizeof(MeshBeacon) - MESH_NETTAG_LEN, b.tag);
  esp_now_send(MESH_BCAST, (const uint8_t*)&b, sizeof(b));
}

static void meshSendBeaconNow(uint8_t rank, uint32_t advertisedIntervalMs) {
  meshSendBeaconEx(rank, advertisedIntervalMs, 0, 0);
}
```

and delete the now-unused `meshWindowDurationMs` variable and the line `meshWindowDurationMs  = b->window_duration_ms;` in `meshHandleBeacon()` (the field changed meaning; nothing else reads it).

- [ ] **Step 6: Parent adoption records sleepiness/depth** — in `meshAdoptParent()` after `meshTrickleReset();` add:

```c
  meshParentSleepy  = (b->flags & MESH_FLAG_SLEEPY) != 0;
  meshMySleepyDepth = meshParentSleepy ? (uint8_t)(b->sleepy_depth + 1) : 0;
  // Adopted a sleepy relay from inside its open window: that IS a catch --
  // anchor on it instead of paying a full-cycle sweep next wake (Task C5).
  if (meshParentSleepy && (b->flags & MESH_FLAG_RX_OPEN)) {
    meshCatchAnchorMs = now - b->window_duration_ms;
    meshCatchSeen = true;
  }
```

(`meshAdoptParent` must be defined after the Step 3 state block; it already is, since the state block sits near the top of the file.)

and in `meshClearParent()` add `meshParentSleepy = false; meshMySleepyDepth = 0;`.

- [ ] **Step 7: Beacon RX rules** — in `meshHandleBeacon()` replace the whole block

```c
  if (b->flags & MESH_FLAG_SLEEPY) {
    if (meshHasParent_ && meshMacEqual(meshParentMac, srcMac))
      meshDropParent("parent became sleepy");
    return;
  }
```

(including the Phase-1 comment above it) with:

```c
  bool senderSleepy = (b->flags & MESH_FLAG_SLEEPY) != 0;
  bool fromParent   = meshHasParent_ && meshMacEqual(meshParentMac, srcMac);

  // A relay with its window open learns its children (and their guard
  // windows) from their beacons -- the data body is AES-GCM and unreadable
  // here (spec §3.1).
  if (senderSleepy && meshRxWindowOpen && b->rank != MESH_RANK_UNROUTED &&
      b->rank > meshMyRank)
    meshCartNoteChild(srcMac, b->guard_hint_q);

  // Catch signal for a CART leaf: its sleepy parent's window is open.
  if (fromParent && senderSleepy && (b->flags & MESH_FLAG_RX_OPEN)) {
    meshCatchAnchorMs = now - b->window_duration_ms;
    meshCatchSeen = true;
  }

  if (senderSleepy) {
    bool usable = meshIsSelfSleepy() && (b->flags & MESH_FLAG_RELAY_CAP) &&
                  (uint16_t)b->sleepy_depth + 1 <= MESH_SLEEPY_RELAY_DEPTH_MAX;
    if (!usable && !fromParent) return;          // never adoptable by us
    if (!usable && fromParent &&
        (uint16_t)b->sleepy_depth + 1 > MESH_SLEEPY_RELAY_DEPTH_MAX) {
      meshDropParent("sleepy parent now too deep");
      return;
    }
    // Prefer an always-on parent at the same or better rank.
    if (!fromParent && meshHasParent_ && !meshParentSleepy && b->rank >= meshParentRank)
      return;
  }
```

(The existing current-parent refresh and strict-rank candidate code below stay unchanged; a full relay that stops advertising `RELAY_CAP` keeps its existing children because `fromParent` bypasses the `usable` gate.)

- [ ] **Step 8: ACK relay gate** — in `meshHandleAck()` replace `if (!meshIsSelfSleepy() && a.ttl > 0) {` with `if ((!meshIsSelfSleepy() || meshRxWindowOpen) && a.ttl > 0) {`.

- [ ] **Step 9: Data relay registers children** — in `meshRelayData()`, right after the dedup check, add:

```c
  if (meshRxWindowOpen) meshCartNoteChild(srcMac, 0);
```

- [ ] **Step 10: RTC persistence** — add to `MeshRtcState` after `uint8_t channel;`:

```c
  bool     parentSleepy;
  uint8_t  sleepyDepth;
```

bump `MESH_RTC_MAGIC` to `0x47534C52UL` ('GSLR'); in `meshRtcPersist()` add `meshRtcState.parentSleepy = meshParentSleepy; meshRtcState.sleepyDepth = meshMySleepyDepth;`; in `meshRtcRestore()`, inside the successful `meshSetParent(...)` branch before `return true;`, add `meshParentSleepy = meshRtcState.parentSleepy; meshMySleepyDepth = meshRtcState.sleepyDepth;`.

- [ ] **Step 11:** Compile all four sketches — expected clean; `sizeof(MeshBeacon)` is 29 (add a temporary `static_assert(sizeof(MeshBeacon) == 29, "")` below the struct and keep it).
- [ ] **Step 12:** Commit `feat(cart): beacon v3, sleepy-parent acceptance under the depth cap, relay child registry`.

### Task C3: Relay receptive window

**Files:** Create `firmware/libraries/GreenhouseMesh/mesh_cart.h`.

**Interfaces:** `meshCartSchedCfg()`, `meshCartOpenWindow()`, `meshCartCloseWindowWhenDue(uint32_t deadlineMs)`; RX beacons are driven by an `esp_timer` so the sketch's blocking send/ACK code keeps running unchanged inside the window.

- [ ] **Step 1: Create `mesh_cart.h`:**

```c
// firmware/libraries/GreenhouseMesh/mesh_cart.h
#pragma once
// CART cycles on top of mesh_node.h. Relay side: receptive window with
// periodic RX_OPEN beacons from an esp_timer, cut-through forwarding is
// meshRelayData() itself (the relay's parent is always-on at depth cap 1).
// Leaf side: see meshCartLeafCycle(). Spec: 2026-09-23-cart-v2-revision.md.
#include <esp_timer.h>
#include <esp_random.h>
#include "mesh_node.h"
#include "mesh_sched.h"

static const MeshSchedCfg* meshCartSchedCfg() {
  static const MeshSchedCfg c = {
    MESH_WAKE_GUARD_MIN_MS, MESH_WAKE_GUARD_MAX_MS, 50, MESH_CART_JITTER_MS,
    MESH_CART_PER_CHILD_MS, MESH_KNOCK_WINDOW_MS, MESH_RELAY_ACK_LINGER_MS,
    MESH_CART_ATTEMPTS, 3, 2, 2500 };
  return &c;
}

static esp_timer_handle_t meshRxTimer = nullptr;

static void meshRxTimerCb(void*) {
  if (!meshRxWindowOpen) return;
  meshSendBeaconEx(meshMyRank, MESH_SLEEP_INTERVAL_MS, MESH_FLAG_RX_OPEN,
                   millis() - meshRxOpenedMs);
}

// Call right after sensor warm-up, every cycle, on a node for which
// meshCanServeAsSleepyParent() is true. The opening instant is the anchor
// children schedule against, so it must sit at a fixed offset from wake.
static void meshCartOpenWindow() {
  meshRxOpenedMs = millis();
  meshRxWindowOpen = true;
  if (!meshRxTimer) {
    esp_timer_create_args_t a = {};
    a.callback = &meshRxTimerCb;
    a.name = "mesh_rx";
    esp_timer_create(&a, &meshRxTimer);
  }
  meshRxTimerCb(nullptr);                                   // first beacon now
  esp_timer_start_periodic(meshRxTimer, MESH_RX_BEACON_PERIOD_MS * 1000ULL);
}

// Keeps the window open for the service time S sized from the children's
// advertised guard windows, lingers for the last ACKs, then closes.
static void meshCartCloseWindowWhenDue(uint32_t deadlineMs) {
  uint32_t S = meshSchedServiceWindowMs(meshCartSchedCfg(), meshCartMaxChildGuardMs(),
                                        meshCartChildCount());
  while (millis() - meshRxOpenedMs < S && millis() < deadlineMs) delay(5);
  if (meshRxTimer) esp_timer_stop(meshRxTimer);
  uint32_t linger = millis();
  while (millis() - linger < MESH_RELAY_ACK_LINGER_MS && millis() < deadlineMs) delay(5);
  meshRxWindowOpen = false;
  meshCartAgeChildren();
}
```

- [ ] **Step 2:** Add `#include "mesh_cart.h"` after `#include "mesh_node.h"` in `edge_node_esp32_c3.ino`, `edge_node_esp32.ino`, `fake_edge_node_esp32_c3.ino`. Compile all four sketches.
- [ ] **Step 3:** Commit `feat(cart): relay receptive window driven by an esp_timer`.

### Task C4: Leaf cycle

**Files:** Modify `firmware/libraries/GreenhouseMesh/mesh_cart.h`.

**Interfaces:**

```c
typedef struct {
  void (*sensorsPower)(bool on);
  void (*readSensors)(SensorReading* out);
  bool (*sendWithConfirm)(const SensorReading* r, uint32_t deadline);   // sketch's: seals + sends
  bool (*resendPacket)(uint8_t* pkt, uint32_t deadline);                // same bytes, same seq
  uint32_t warmupMs;
} MeshCartHooks;
static uint32_t meshCartLeafCycle(const MeshCartHooks* h, uint32_t deadlineMs); // returns sleep ms
static uint32_t meshCartAnchorAndSleepMs(uint32_t leadMs);                      // adopted mid-window
```

Retries must resend the **same sealed bytes**: re-sealing would give the
reading a new `seq`, and if the first attempt actually arrived (only its L2
ACK was lost) the Pi would store the reading twice.

- [ ] **Step 1: Append to `mesh_cart.h`:**

```c
typedef struct {
  void (*sensorsPower)(bool on);
  void (*readSensors)(SensorReading* out);
  bool (*sendWithConfirm)(const SensorReading* r, uint32_t deadline);
  bool (*resendPacket)(uint8_t* pkt, uint32_t deadline);
  uint32_t warmupMs;
} MeshCartHooks;

#define MESH_CART_RTC_MAGIC 0x43415254UL   // 'CART'
RTC_DATA_ATTR static uint32_t       meshSchedMagic;
RTC_DATA_ATTR static uint8_t        meshSchedParent[6];
RTC_DATA_ATTR static MeshSchedState meshSched;

// Scheduler state is only meaningful for the parent it was learned against.
static void meshCartSchedValidate() {
  if (meshSchedMagic != MESH_CART_RTC_MAGIC || !meshMacEqual(meshSchedParent, meshParentMac)) {
    meshSchedReset(&meshSched, meshCartSchedCfg());
    memcpy(meshSchedParent, meshParentMac, 6);
    meshSchedMagic = MESH_CART_RTC_MAGIC;
  }
}

// Seal the reading and keep it for later (window missed).
static void meshCartBufferReading(const SensorReading* r) {
  if (!meshLoadKeys()) return;
  uint8_t body[MESH_BODY_LEN];
  meshPackBody(body, r->temperature, r->humidity, r->soil_moisture, meshBatteryMv,
               meshParentMac, (int8_t)meshParentRssi);
  uint8_t packet[MESH_PACKET_LEN];
  if (meshSeal(packet, meshAppKey, meshNetKey, meshSelfMac, meshDataSeq++,
               meshStoreBootCount(), MESH_FLAG_SLEEPY, meshMyRank, meshTxTtl(), body))
    meshBufferPush(packet);
}

static uint32_t meshCartLeafCycle(const MeshCartHooks* h, uint32_t deadlineMs) {
  const MeshSchedCfg* c = meshCartSchedCfg();
  uint32_t cycle = meshCycleMs();
  meshCartSchedValidate();

  // Sensors first (the relay's window opens right after ITS warm-up too).
  h->sensorsPower(true);
  uint32_t t0 = millis();
  while (millis() - t0 < h->warmupMs && millis() < deadlineMs) delay(10);
  SensorReading r;
  h->readSensors(&r);
  h->sensorsPower(false);

  uint16_t gUsed = meshSched.guardMs;
  uint32_t listenEnd = meshSched.anchored ? h->warmupMs + gUsed
                                          : cycle + meshSchedServiceWindowMs(c, MESH_WAKE_GUARD_MAX_MS, MESH_MAX_SLEEPY_CHILDREN);
  meshCatchSeen = false;
  while (!meshCatchSeen && millis() < listenEnd && millis() < deadlineMs + (meshSched.anchored ? 0 : cycle))
    delay(2);

  if (!meshCatchSeen) {
    MeshSchedAction a = meshSchedOnMiss(&meshSched, c);
    Serial.printf("[cart] window missed (G=%u ms, run=%u, action=%d)\n", gUsed, meshSched.missRun, (int)a);
    meshCartBufferReading(&r);
    meshMyGuardHintQ = (uint8_t)((meshSched.guardMs + 249) / 250);
    // Virtual anchor = where the window was predicted: the error carries over.
    uint32_t virtualAnchor = meshSchedPredictedCatchMs(&meshSched, h->warmupMs);
    if (a == MESH_SCHED_SWEEP) return MESH_MIN_SLEEP_MS;    // next wake sweeps
    return meshSchedSleepMs(&meshSched, virtualAnchor, millis(), cycle, h->warmupMs, MESH_MIN_SLEEP_MS);
  }

  uint32_t anchor = meshCatchAnchorMs;
  if (!meshSched.anchored) {
    meshSchedOnAnchor(&meshSched, c);                        // sweep caught
  } else {
    int32_t err = (int32_t)anchor - (int32_t)meshSchedPredictedCatchMs(&meshSched, h->warmupMs);
    meshSchedOnCatch(&meshSched, c, err);
  }
  meshMyGuardHintQ = (uint8_t)((meshSched.guardMs + 249) / 250);

  // Announce ourselves (guard hint) while the relay listens, then send after
  // a random jitter, up to MESH_CART_ATTEMPTS times (spec §3.5).
  meshSendBeaconNow(meshMyRank, cycle);
  bool delivered = false;
  uint8_t pending[MESH_PACKET_LEN];
  bool havePending = false;
  for (int attempt = 0; attempt < MESH_CART_ATTEMPTS && !delivered && millis() < deadlineMs; attempt++) {
    uint32_t j = meshSchedJitterMs(c, esp_random());
    uint32_t tj = millis();
    while (millis() - tj < j) delay(1);
    if (!havePending) {
      delivered = h->sendWithConfirm(&r, deadlineMs);   // seals once; arms the ACK wait
      if (meshLastPktValid) { memcpy(pending, meshLastPkt, MESH_PACKET_LEN); havePending = true; }
    } else {
      delivered = h->resendPacket(pending, deadlineMs); // same bytes, same seq
    }
  }
  if (delivered) {
    uint32_t w = millis();
    while (meshAckResult() == -1 && millis() - w < MESH_APP_ACK_WAIT_MS && millis() < deadlineMs) delay(5);
    int ack = meshAckResult();
    Serial.printf("[cart] delivered, app ack=%d\n", ack);
  } else if (havePending) {
    meshBufferPush(pending);
    Serial.println("[cart] caught window but send failed — reading buffered");
  }
  return meshSchedSleepMs(&meshSched, anchor, millis(), cycle, h->warmupMs, MESH_MIN_SLEEP_MS);
}

// Called by the Phase-1 path when it has just adopted a sleepy relay from
// inside that relay's open window (meshAdoptParent set meshCatchSeen): the
// adoption instant is a valid anchor, so no sweep is needed next wake.
static uint32_t meshCartAnchorAndSleepMs(uint32_t leadMs) {
  meshCartSchedValidate();
  meshSchedOnAnchor(&meshSched, meshCartSchedCfg());
  meshMyGuardHintQ = (uint8_t)((meshSched.guardMs + 249) / 250);
  return meshSchedSleepMs(&meshSched, meshCatchAnchorMs, millis(), meshCycleMs(),
                          leadMs, MESH_MIN_SLEEP_MS);
}
```

Notes for the implementer: `sendWithConfirm` calls `meshSendReading()`, which flushes any buffered readings first (cut-through at the relay). A sweep (`anchored == 0`) listens up to one full cycle plus a maximal window — it is the one wake allowed to exceed the leaf backstop, bounded by `cycle`.

- [ ] **Step 2:** Compile all four sketches. **Step 3:** Commit `feat(cart): leaf cycle — guard listen, jittered retries, re-anchoring`.

### Task C5: Wire CART into the three sleepy sketches

**Files:** Modify `edge_node_esp32_c3.ino`, `edge_node_esp32.ino`, `fake_edge_node_esp32_c3.ino` (`goToSleep`, `runSleepyCycle`).

- [ ] **Step 1: Explicit-duration sleep** — in each sketch add after `goToSleep()`:

```cpp
void goToSleepFor(uint8_t channel, uint32_t sleepMs) {
  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);
  meshRtcPersist(channel);
  Serial.printf("[sleep] CART, sleeping %lums\n", (unsigned long)sleepMs);
  Serial.flush();
  esp_sleep_enable_timer_wakeup((uint64_t)sleepMs * 1000ULL);
  esp_deep_sleep_start();
}

static void cartSensorsPower(bool on) {
  digitalWrite(SOIL_PWR_PIN, on ? HIGH : LOW);
  digitalWrite(DHT_PWR_PIN,  on ? HIGH : LOW);
}
```

and a `cartReadSensors(SensorReading* r)` that performs exactly what the sketch's `runSleepyCycle` does today to fill `r` (C3/WROOM: `r->temperature = dht.readTemperature(); r->humidity = dht.readHumidity(); r->soil_moisture = soilPercent(analogRead(SOIL_DATA_PIN));`; fake: `readFakeSensors(r);`), followed by `meshSetBatteryMv(readBatteryMv());` (fake: `meshSetBatteryMv(readFakeBatteryMv());`). Add the same-bytes resend hook (uses the sketch's own `g_lastTxStatus`, set by `onDataSent`):

```cpp
static bool cartResendPacket(uint8_t* pkt, uint32_t deadline) {
  g_lastTxStatus = -1;
  pkt[15] = meshTxTtl();                       // outside both tags (Task A2)
  if (!meshUnicastToParent(pkt)) return false;
  uint32_t t = millis();
  while (g_lastTxStatus == -1 && millis() - t < MESH_TX_CONFIRM_WAIT_MS && millis() < deadline) delay(5);
  return g_lastTxStatus == 1;
}
```

- [ ] **Step 2: Leaf branch** — in `runSleepyCycle()`, immediately after the `meshRtcRestore()`/`Serial.printf("[wake] rtc restore...")` lines, insert:

```cpp
  if (meshHasParent() && meshParentSleepy) {
    static const MeshCartHooks hooks = { cartSensorsPower, cartReadSensors,
                                         sendWithConfirm, cartResendPacket, SENSOR_WARMUP_MS };
    uint32_t sleepMs = meshCartLeafCycle(&hooks, MESH_WAKE_MAX_AWAKE_MS);
    goToSleepFor(ch, sleepMs);   // never returns
  }
```

  and, immediately before the final `goToSleep(ch);` of the Phase-1 path (after the relay close of Step 3 below):

```cpp
  // Orphan that adopted a sleepy relay from inside its open window during
  // this wake's discovery: anchor now, skip next wake's full-cycle sweep.
  if (meshHasParent() && meshParentSleepy && meshCatchSeen)
    goToSleepFor(ch, meshCartAnchorAndSleepMs(SENSOR_WARMUP_MS));
```

- [ ] **Step 3: Relay branch** — still in `runSleepyCycle()`, directly after the sensor warm-up wait loop (`while (millis() - warmupStart < SENSOR_WARMUP_MS && ...)`), insert:

```cpp
  bool relaying = meshCanServeAsSleepyParent();
  uint32_t relayDeadline = relaying
      ? meshSchedRelayBackstopMs(meshCartSchedCfg(), MESH_MAX_SLEEPY_CHILDREN)
      : deadline;
  if (relaying) meshCartOpenWindow();
```

change the existing `sendWithConfirm(&r, deadline)` call and the retry loops' `deadline` to `relayDeadline`, and immediately before the final `goToSleep(ch);` insert:

```cpp
  if (relaying) meshCartCloseWindowWhenDue(relayDeadline);
```

- [ ] **Step 4:** Compile all four sketches; build once more with `MESH_SLEEPY_RELAY_DEPTH_MAX 0` (edit, compile, revert) to confirm the rollback build compiles.
- [ ] **Step 5:** Commit `feat(cart): sleepy sketches run the relay window / leaf cycle`.

### Task C6: Relay role in `/mesh` telemetry

**Files:** Modify `firmware/libraries/GreenhouseMesh/mesh_node.h` (`meshSendReading()` flags), `mesh_cart.h` (`meshCartBufferReading` flags), `pi/scripts/serial_bridge.py` (`handle_frame` mesh payload). Test: `pi/tests/test_serial_bridge_mesh.py`.

Header flags are inside the GCM AAD (authenticated, cleartext): bit1 = relaying this cycle, bits2–3 = sleepy depth.

- [ ] **Step 1: Failing test:**

```python
def test_mesh_record_reports_relay_role_and_depth(state):
    body = mp.pack_body(21.5, 60.0, 42.0, 3700, PARENT, -67)
    raw = mc.seal_packet(APP, NET, MAC, seq=5, boot_count=1,
                         flags=0x01 | 0x02 | (1 << 2), rank=2, ttl=4, body=body)
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': raw.hex()}, state)
    payload = json.loads([p for t, p, _ in c.published if t.endswith('/mesh')][0])
    assert payload['relay'] is True and payload['sleepy_depth'] == 1
```

- [ ] **Step 2:** Run → FAIL (`KeyError`).
- [ ] **Step 3:** In the success-path `_publish_mesh` dict add `'relay': bool(header.flags & 0x02), 'sleepy_depth': (header.flags >> 2) & 0x03,`; in the auth-failure `_publish_mesh` dict add the same two keys (header is cleartext there too).
- [ ] **Step 4:** Test → PASS; full suite.
- [ ] **Step 5: Firmware** — in `meshSendReading()` replace `meshIsSelfSleepy() ? MESH_FLAG_SLEEPY : 0` with

```c
(uint8_t)((meshIsSelfSleepy() ? MESH_FLAG_SLEEPY : 0) |
          (meshRxWindowOpen ? 0x02 : 0) | ((meshMySleepyDepth & 0x03) << 2))
```

and in `meshCartBufferReading` replace `MESH_FLAG_SLEEPY` with `(uint8_t)(MESH_FLAG_SLEEPY | ((meshMySleepyDepth & 0x03) << 2))`. Compile all four.
- [ ] **Step 6:** Commit `feat(cart): /mesh telemetry reports relay role and sleepy depth`.

---

## Part D — Validation gates (procedures)

### Task D1: Gate 1 — parity (`MESH_SLEEPY_RELAY_DEPTH_MAX = 0`)

- [ ] Flag-day reflash of bridge + all nodes with the Part C firmware built with depth 0.
- [ ] 24 h: every node's readings, `last_ack`, `/mesh` records and battery behaviour match a Phase 1 baseline day (same counts ± 1 %, same awake-time log lines). Any difference = stop and fix before Gate 2.

### Task D2: Gate 2 — isolated pair

- [ ] Depth 1 build. One sleepy relay R in bridge range; one sleepy leaf C built with `#define MESH_TEST_IGNORE_BRIDGE` so it must use R.
- [ ] Run `pi/tools/drift_logger.py` plus serial capture of both for ≥ 500 cycles.
- [ ] Pass: C's `[cart]` catch rate ≥ 99 %; zero lost readings (Pi `seq` continuity per MAC); every C reading `last_ack = accepted` in-cycle; awake times within ±20 % of spec §3.4; R never exceeds its backstop.
- [ ] Negative tests: power R off for 3 cycles → C reaches `action=2` (sweep) and re-anchors when R returns; flash C with a 60 s test interval while R runs 300 s → C follows R's advertised T.

### Task D3: Gate 3 — fleet, 7 days

- [ ] All nodes, depth 1. Pass: Gate 2 criteria per node; no node in repeated sweeps (> 1/week); relay `/mesh` shows `relay: true`, children `sleepy_depth: 1`.
- [ ] Update `HANDOFF.md` and the report §21 with measured numbers; commit.

## Self-review notes

- Spec coverage: §3.1 → A3, A4, C2; §3.2 → C2 (ACK gate, cut-through via `meshRelayData`), C3, C5; §3.3 → C1, C4; §3.4 → B4 decision; §3.5 → C1 (jitter), C2 (admission), C4 (attempts); §3.6 → A2, A5; §3.7 → A1, C1 (backstop), C4 (sweep, extended); §4 catalogue → A4 (caps), C2 (`meshCycleMs`, `static_assert`), C1 (backstop), C6 (telemetry); §6 gates → B4, D1–D3.
- Order: A1–A5 are independent of each other; B1–B3 independent; B4 needs B1–B3; C1 before C2–C4; C5 after C3/C4; C6 after C2.
- Nothing here executes automatically — the user has asked for plans only.
