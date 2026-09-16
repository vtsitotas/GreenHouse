# Application-Layer Mesh ACK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a sleepy edge node a real end-to-end delivery confirmation from the Pi (accepted / explicitly rejected / no answer), instead of only the ESP-NOW link-layer ack to its immediate parent it has today.

**Architecture:** A new broadcast packet type, `MeshAck` (20 bytes, nettag-authenticated), flows Pi → bridge (over the existing UART link) → mesh (flooded, TTL-bounded, deduped — the same pattern the uplink data path already uses, no new routing table). A sleepy node arms a "waiting for this seq" flag right before it sends, then polls for up to `MESH_APP_ACK_WAIT_MS` after its existing L2 confirm succeeds, before deciding whether to reset its retry counter.

**Tech Stack:** Arduino/C++ (ESP32/ESP32-C3, arduino-cli), Python 3 (Pi), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-16-mesh-app-ack-design.md` — read it first; this plan does not repeat the reasoning behind each decision, only what to build.

## Global Constraints

- Firmware: match the existing project style in `firmware/libraries/GreenhouseMesh/*.h` and the `.ino` sketches — no new libraries, packed structs with explicit byte layouts (never rely on compiler struct packing for wire formats), `static` file-scope functions in the header-only library files.
- No firmware unit test framework exists in this codebase — verification is `arduino-cli compile` (compile-clean = task done) plus, at the end of this plan, real hardware bench testing (not automatable, not part of any single task below).
- `arduino-cli compile` FQBNs: edge nodes and bridge are all **ESP32-C3** — `--fqbn esp32:esp32:esp32c3` (confirmed via `esptool` earlier this project; the bridge sketch folder is misleadingly named `bridge_esp32` but the board is a C3). `edge_node_esp32.ino` (the WROOM variant, Task 3) uses `--fqbn esp32:esp32:esp32`.
- Python: `python -m pytest pi/tests/ -v` from the repo root after every Pi-side task — matches `.github/workflows/ci.yml:38` exactly.
- Every wire-format change is length-discriminated, never a type byte inside the payload — this is the existing convention (`onDataRecv` dispatches purely on `len`) and `MeshAck`'s 20 bytes is already unique among the four message shapes (8 / 20 / 27 / 61) — do not change any existing struct's size while implementing this.
- Commit after each task, following the existing repo convention (see recent commits `4280668`/`dcd421e`/`f73f9af` for message style).

---

## Task 1: `MeshAck` protocol core in the shared mesh library

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_config.h` (add one constant)
- Modify: `firmware/libraries/GreenhouseMesh/mesh_node.h:14-22` (constants + struct), `:100-103` (new dedup cache, alongside the existing one), `:371-407` (`meshSendReading()` — one-line integration)

**Interfaces:**
- Produces (consumed by Tasks 2-5): `MeshAck` struct, `MESH_ACK_MARKER`/`MESH_ACK_OK`/`MESH_ACK_REJECTED`/`MESH_ACK_TTL` constants, `bool meshBuildAck(uint8_t* out, const uint8_t* targetMac, uint16_t seq, uint8_t status)`, `void meshHandleAck(const uint8_t* data, int len)` (no source-MAC parameter needed — the packet's own `target_mac` field is what matters, not who relayed it), `int meshAckResult()` (returns `-1` / `MESH_ACK_OK` / `MESH_ACK_REJECTED`).
- Consumes: existing `meshCmacTruncated()` (from `mesh_crypto.h`, already included), existing `meshLoadKeys()`, `meshNetKey`, `meshSelfMac`, `meshIsSelfSleepy()`, `meshMacEqual()`, `MESH_BCAST`, `MESH_NETTAG_LEN`, `MESH_MAGIC_V2`, `MESH_DEDUP_WINDOW_MS` (all already defined earlier in `mesh_node.h`/`mesh_packet.h`).

- [ ] **Step 1: Add the new timing constant**

In `firmware/libraries/GreenhouseMesh/mesh_config.h`, the current block (lines 56-64) reads:

```c
#define MESH_SLEEP_INTERVAL_MS    60000UL   // 1 min test duty cycle (change back to 900000UL for 15 min production)
#define MESH_WAKE_DISCOVERY_MS    5000UL    // orphaned-wake listen window before
                                            // giving up and buffering the reading
#define MESH_TX_CONFIRM_WAIT_MS   500UL     // wait for the ESP-NOW send callback
                                            // before judging a unicast delivered
#define MESH_WAKE_MAX_AWAKE_MS    10000UL   // hard backstop: persist state and
                                            // sleep no matter what path we're on
#define MESH_MIN_SLEEP_MS         1000UL    // floor after subtracting awake time
                                            // (never arm a 0/negative timer)
```

Add one new line right after `MESH_TX_CONFIRM_WAIT_MS`:

```c
#define MESH_SLEEP_INTERVAL_MS    60000UL   // 1 min test duty cycle (change back to 900000UL for 15 min production)
#define MESH_WAKE_DISCOVERY_MS    5000UL    // orphaned-wake listen window before
                                            // giving up and buffering the reading
#define MESH_TX_CONFIRM_WAIT_MS   500UL     // wait for the ESP-NOW send callback
                                            // before judging a unicast delivered
#define MESH_APP_ACK_WAIT_MS      2000UL    // wait for the Pi's real accept/reject
                                            // ack after L2 delivery succeeds —
                                            // covers one UART round trip plus a
                                            // few mesh hops each way, comfortably
                                            // under MESH_WAKE_MAX_AWAKE_MS
#define MESH_WAKE_MAX_AWAKE_MS    10000UL   // hard backstop: persist state and
                                            // sleep no matter what path we're on
#define MESH_MIN_SLEEP_MS         1000UL    // floor after subtracting awake time
                                            // (never arm a 0/negative timer)
```

- [ ] **Step 2: Add the `MeshAck` wire format and constants**

In `firmware/libraries/GreenhouseMesh/mesh_node.h`, find the `MeshJoinBeacon` block (currently lines 51-66):

```c
typedef struct __attribute__((packed)) {
  uint8_t magic;    // MESH_MAGIC_V2
  uint8_t marker;   // MESH_JOIN_MARKER — distinguishes this from a real beacon
  uint8_t mac[6];
} MeshJoinBeacon;   // 8 bytes

static const uint8_t MESH_BCAST[6] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };
```

Insert a new block immediately after it (before `static const uint8_t MESH_BCAST...` stays where it is — insert between the `MeshJoinBeacon` struct and the `MESH_BCAST` line):

```c
typedef struct __attribute__((packed)) {
  uint8_t magic;    // MESH_MAGIC_V2
  uint8_t marker;   // MESH_JOIN_MARKER — distinguishes this from a real beacon
  uint8_t mac[6];
} MeshJoinBeacon;   // 8 bytes

// ── Application-layer ACK/NACK (flood, not routed) ────────────────────────────
// A fourth broadcast message shape, distinguished purely by length like the
// other three (8 / 20 / 27 / 61 bytes) — no type byte needed inside the
// payload. Originates only at the bridge (the Pi is the only thing that knows
// whether a reading was actually accepted); every other node either consumes
// it (if it's the addressed target) or floods it one hop further (if it's
// not sleepy) — see meshHandleAck() below.
#define MESH_ACK_MARKER    0x41   // 'A' — distinct from MESH_JOIN_MARKER (0x4A)
#define MESH_ACK_OK        1      // 0 is deliberately unused: an all-zero or
#define MESH_ACK_REJECTED  2      // uninitialized status is never mistaken
                                  // for a valid outcome
#define MESH_ACK_TTL       6      // flood hop budget; generous relative to
                                  // this fleet's field-observed 1-2 hops

typedef struct __attribute__((packed)) {
  uint8_t  magic;                 // byte 0:      MESH_MAGIC_V2
  uint8_t  marker;                // byte 1:      MESH_ACK_MARKER
  uint8_t  target_mac[6];         // bytes 2-7:   which origin node this concerns
  uint16_t seq;                   // bytes 8-9:   which reading (origin's own seq)
  uint8_t  status;                // byte 10:     MESH_ACK_OK or MESH_ACK_REJECTED
  uint8_t  ttl;                   // byte 11:     decremented per hop, NOT covered
                                  //              by the tag (mutates every hop,
                                  //              same reason the data packet's
                                  //              ttl is excluded from its AAD)
  uint8_t  tag[MESH_NETTAG_LEN];  // bytes 12-19: CMAC-AES(NetKey) over bytes 0-10
} MeshAck;                        // 20 bytes — unique length among all 4 shapes

static const uint8_t MESH_BCAST[6] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };
```

- [ ] **Step 3: Add the ack-dedup cache and pending-ack wait state**

In `firmware/libraries/GreenhouseMesh/mesh_node.h`, find the existing data-dedup cache declaration (currently lines 100-103):

```c
typedef struct { uint8_t mac[6]; uint16_t seq; uint32_t ms; bool used; } MeshDedupEntry;
static MeshDedupEntry meshDedup[MESH_DEDUP_CACHE_SIZE];
static int meshDedupNext = 0;
```

Insert a new, deliberately separate cache right after it (sharing the existing `meshDedup` array would let an uplink data packet and a downlink ack for the same `(mac, seq)` pair falsely deduplicate against each other):

```c
typedef struct { uint8_t mac[6]; uint16_t seq; uint32_t ms; bool used; } MeshDedupEntry;
static MeshDedupEntry meshDedup[MESH_DEDUP_CACHE_SIZE];
static int meshDedupNext = 0;

// Separate from meshDedup above on purpose — see the comment on MeshAck.
#define MESH_ACK_DEDUP_CACHE_SIZE 8
typedef struct { uint8_t mac[6]; uint16_t seq; uint32_t ms; bool used; } MeshAckDedupEntry;
static MeshAckDedupEntry meshAckDedup[MESH_ACK_DEDUP_CACHE_SIZE];
static int meshAckDedupNext = 0;

// Set by meshSendReading() right before it unicasts to the parent; polled by
// the wake-cycle loop in each edge sketch. -1 (via meshAckResult()) means "no
// answer yet". Only one outstanding wait at a time — a sleepy node only ever
// has one reading in flight.
static uint16_t meshWaitingAckSeq    = 0;
static bool     meshWaitingAckSeen   = false;
static uint8_t  meshWaitingAckStatus = 0;
```

- [ ] **Step 4: Add `meshAckDedupSeen()`, `meshBuildAck()`, `meshHandleAck()`, and the wait accessors**

In `firmware/libraries/GreenhouseMesh/mesh_node.h`, find the existing `meshDedupSeen()` function (currently lines 330-344):

```c
// ── De-dup cache ──────────────────────────────────────────────────────────────
static bool meshDedupSeen(const uint8_t* originMac, uint16_t seq) {
  uint32_t now = millis();
  for (int i = 0; i < MESH_DEDUP_CACHE_SIZE; i++) {
    if (!meshDedup[i].used) continue;
    if (now - meshDedup[i].ms >= MESH_DEDUP_WINDOW_MS) continue;
    if (meshDedup[i].seq == seq &&
        meshMacEqual(meshDedup[i].mac, originMac)) return true;
  }
  memcpy(meshDedup[meshDedupNext].mac, originMac, 6);
  meshDedup[meshDedupNext].seq  = seq;
  meshDedup[meshDedupNext].ms   = now;
  meshDedup[meshDedupNext].used = true;
  meshDedupNext = (meshDedupNext + 1) % MESH_DEDUP_CACHE_SIZE;
  return false;
}
```

Insert a new block immediately after it, before the `// ── Data path` comment that follows:

```c
static bool meshAckDedupSeen(const uint8_t* targetMac, uint16_t seq) {
  uint32_t now = millis();
  for (int i = 0; i < MESH_ACK_DEDUP_CACHE_SIZE; i++) {
    if (!meshAckDedup[i].used) continue;
    if (now - meshAckDedup[i].ms >= MESH_DEDUP_WINDOW_MS) continue;
    if (meshAckDedup[i].seq == seq &&
        meshMacEqual(meshAckDedup[i].mac, targetMac)) return true;
  }
  memcpy(meshAckDedup[meshAckDedupNext].mac, targetMac, 6);
  meshAckDedup[meshAckDedupNext].seq  = seq;
  meshAckDedup[meshAckDedupNext].ms   = now;
  meshAckDedup[meshAckDedupNext].used = true;
  meshAckDedupNext = (meshAckDedupNext + 1) % MESH_ACK_DEDUP_CACHE_SIZE;
  return false;
}

static void meshArmAckWait(uint16_t seq) {
  meshWaitingAckSeq  = seq;
  meshWaitingAckSeen = false;
}

// -1 = no answer yet, else MESH_ACK_OK or MESH_ACK_REJECTED.
static int meshAckResult() {
  return meshWaitingAckSeen ? (int)meshWaitingAckStatus : -1;
}

// Builds and signs a fresh MeshAck into out (must point at >= sizeof(MeshAck)
// bytes). Only ever called by the bridge, which always originates an ack —
// nothing is upstream of it to relay one to it.
static bool meshBuildAck(uint8_t* out, const uint8_t* targetMac, uint16_t seq,
                         uint8_t status) {
  if (!meshLoadKeys()) return false;
  MeshAck a;
  a.magic  = MESH_MAGIC_V2;
  a.marker = MESH_ACK_MARKER;
  memcpy(a.target_mac, targetMac, 6);
  a.seq    = seq;
  a.status = status;
  a.ttl    = MESH_ACK_TTL;
  if (!meshCmacTruncated(meshNetKey, (const uint8_t*)&a, sizeof(a) - MESH_NETTAG_LEN,
                         a.tag)) return false;
  memcpy(out, &a, sizeof(a));
  return true;
}

// Receive-side: verify, dedup, consume if it's mine, relay one hop further if
// I'm not sleepy. Mirrors meshRelayData()'s shape for the uplink data path.
static void meshHandleAck(const uint8_t* data, int len) {
  if (len != (int)sizeof(MeshAck)) return;
  if (!meshLoadKeys()) return;

  MeshAck a;
  memcpy(&a, data, sizeof(a));

  uint8_t expect[MESH_NETTAG_LEN];
  if (!meshCmacTruncated(meshNetKey, data, sizeof(a) - MESH_NETTAG_LEN, expect)) return;
  uint8_t diff = 0;
  for (int i = 0; i < MESH_NETTAG_LEN; i++) diff |= expect[i] ^ a.tag[i];
  if (diff != 0) return;   // forged/corrupt — same silent-drop posture as a bad beacon tag

  if (meshAckDedupSeen(a.target_mac, a.seq)) return;

  if (meshMacEqual(a.target_mac, meshSelfMac) && a.seq == meshWaitingAckSeq) {
    // Idempotent overwrite — this ack can legitimately arrive more than once
    // (the same broadcast heard via two different relays), and a second
    // identical write here must be harmless, never a counter/toggle.
    meshWaitingAckStatus = a.status;
    meshWaitingAckSeen   = true;
  }

  if (!meshIsSelfSleepy() && a.ttl > 0) {
    uint8_t fwd[sizeof(MeshAck)];
    memcpy(fwd, data, sizeof(fwd));
    fwd[11] = a.ttl - 1;   // ttl is byte offset 11 — see the MeshAck layout above
    esp_now_send(MESH_BCAST, fwd, sizeof(fwd));
  }
}
```

- [ ] **Step 5: Arm the wait when a reading is actually unicast**

In `firmware/libraries/GreenhouseMesh/mesh_node.h`, `meshSendReading()` (currently lines 371-407) ends with:

```c
  if (!meshHasParent_) {
    meshBufferPush(packet);
    meshLastPktValid = false;
    Serial.printf("[mesh] unrouted — reading buffered (%d queued)\n", meshBufCount);
    return;
  }
  if (meshBufCount > 0) {
    Serial.printf("[mesh] routed again — flushing %d buffered readings\n", meshBufCount);
    meshFlushBuffer();
  }
  meshUnicastToParent(packet);
}
```

Change the last line only:

```c
  if (!meshHasParent_) {
    meshBufferPush(packet);
    meshLastPktValid = false;
    Serial.printf("[mesh] unrouted — reading buffered (%d queued)\n", meshBufCount);
    return;
  }
  if (meshBufCount > 0) {
    Serial.printf("[mesh] routed again — flushing %d buffered readings\n", meshBufCount);
    meshFlushBuffer();
  }
  meshArmAckWait((uint16_t)(meshDataSeq - 1));  // meshDataSeq++ above already
                                                // advanced past the seq this
                                                // packet actually used
  meshUnicastToParent(packet);
}
```

Note: deliberately only the direct-send path arms the wait, not `meshFlushBuffer()`'s retry path (a buffered packet's seq would need extracting from its raw bytes, and the retry path already has its own existing L2-only confirm/discovery dance — see Task 2 Step 3 for how the two paths stay separate).

- [ ] **Step 6: Compile-verify (no sketch calls the new code yet — this proves it's syntactically sound)**

Run: `arduino-cli compile --fqbn esp32:esp32:esp32c3 firmware/edge_node_esp32_c3`
Expected: compiles with no errors (dead-code warnings about unused `meshBuildAck`/`meshHandleAck` are fine and expected — Task 2 wires them in).

- [ ] **Step 7: Commit**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_config.h firmware/libraries/GreenhouseMesh/mesh_node.h
git commit -m "$(cat <<'EOF'
feat: add MeshAck protocol core (application-layer mesh ack/nack)

New broadcast packet type, flood-relayed with its own dedup cache
(deliberately separate from the uplink data dedup, to avoid a shared
(mac,seq) pair from the two directions falsely colliding). Not yet
wired into any sketch's onDataRecv or wake cycle -- that's the next
several tasks. See docs/superpowers/specs/2026-09-16-mesh-app-ack-design.md.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire the ACK wait into `edge_node_esp32_c3.ino`

**Files:**
- Modify: `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:65-87` (`onDataRecv`), `:183-212` (`runSleepyCycle`)

**Interfaces:**
- Consumes: `meshHandleAck()`, `meshAckResult()`, `MESH_ACK_OK`, `MESH_ACK_REJECTED`, `MESH_APP_ACK_WAIT_MS` (all from Task 1).

- [ ] **Step 1: Add the `MeshAck` dispatch branch to `onDataRecv`**

In `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino`, the current function (lines 65-87) reads:

```cpp
void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  uint32_t now = millis();
  int rssi = info->rx_ctrl ? info->rx_ctrl->rssi : -127;

  // Check for a provisioning blob (33 bytes, not a beacon or data packet)
  if (len == MESH_PROVISION_LEN && !meshStoreIsProvisioned()) {
    if (meshHandleProvision(data, len)) {
      Serial.println("[edge] provisioned — will boot into normal mode");
      delay(500);
      ESP.restart();
    }
    return;
  }

  if (len == (int)sizeof(MeshBeacon)) {
    MeshBeacon b;
    memcpy(&b, data, sizeof(b));
    if (b.magic == MESH_MAGIC_V2) meshHandleBeacon(info->src_addr, &b, rssi, now);
  } else if (len == MESH_PACKET_LEN) {
    // Some child picked us as its parent — relay its packet toward the bridge.
    meshRelayData(info->src_addr, data, len);
  }
}
```

Add one `else if` branch:

```cpp
void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  uint32_t now = millis();
  int rssi = info->rx_ctrl ? info->rx_ctrl->rssi : -127;

  // Check for a provisioning blob (33 bytes, not a beacon or data packet)
  if (len == MESH_PROVISION_LEN && !meshStoreIsProvisioned()) {
    if (meshHandleProvision(data, len)) {
      Serial.println("[edge] provisioned — will boot into normal mode");
      delay(500);
      ESP.restart();
    }
    return;
  }

  if (len == (int)sizeof(MeshBeacon)) {
    MeshBeacon b;
    memcpy(&b, data, sizeof(b));
    if (b.magic == MESH_MAGIC_V2) meshHandleBeacon(info->src_addr, &b, rssi, now);
  } else if (len == MESH_PACKET_LEN) {
    // Some child picked us as its parent — relay its packet toward the bridge.
    meshRelayData(info->src_addr, data, len);
  } else if (len == (int)sizeof(MeshAck)) {
    meshHandleAck(data, len);
  }
}
```

- [ ] **Step 2: Compile-verify the dispatch change alone**

Run: `arduino-cli compile --fqbn esp32:esp32:esp32c3 firmware/edge_node_esp32_c3`
Expected: compiles with no errors.

- [ ] **Step 3: Wait for the app-level ack after a successful L2 delivery, and let it drive `g_unconfirmedWakes`**

In `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino`, `runSleepyCycle()` currently has this section (lines 183-212):

```cpp
  bool delivered = sendWithConfirm(&r, deadline);
  Serial.printf("[wake] delivered=%d hasParent=%d\n", delivered, meshHasParent());

  if (!delivered && millis() < deadline) {
    if (meshHasParent()) meshDropParent("wake tx unconfirmed");
    meshRequeueLastReading();

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

  bool confirmed = delivered || (meshHasParent() && g_lastTxStatus == 1);
  if (confirmed) g_unconfirmedWakes = 0;
  else if (g_unconfirmedWakes < 250) g_unconfirmedWakes++;

  goToSleep(ch);  // never returns
```

Change it to:

```cpp
  bool delivered = sendWithConfirm(&r, deadline);
  Serial.printf("[wake] delivered=%d hasParent=%d\n", delivered, meshHasParent());

  // Only the direct-send path (not the requeue/rediscovery retry below) waits
  // for the app-level ack: this is specifically the "L2 delivered, but did
  // the Pi actually accept it" question, and the retry path below already
  // has its own, unchanged, L2-only confirm dance for "is there a route at
  // all" -- a different failure class with a different existing answer.
  int appAckStatus = -1;
  if (delivered) {
    uint32_t ackWaitStart = millis();
    while (meshAckResult() == -1 &&
           millis() - ackWaitStart < MESH_APP_ACK_WAIT_MS &&
           millis() < deadline) {
      delay(5);
    }
    appAckStatus = meshAckResult();
    if (appAckStatus == MESH_ACK_OK) {
      Serial.println("[wake] Pi confirmed reading accepted");
    } else if (appAckStatus == MESH_ACK_REJECTED) {
      Serial.println("[wake] Pi rejected reading — check AppKey");
    } else {
      Serial.println("[wake] no app-level ack within wait window");
    }
  }

  if (!delivered && millis() < deadline) {
    if (meshHasParent()) meshDropParent("wake tx unconfirmed");
    meshRequeueLastReading();

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

  // Direct-send path: only a real app-level MESH_ACK_OK counts as confirmed
  // now. Retry path: unchanged, L2-only -- it never got an app-ack wait.
  bool confirmed = delivered ? (appAckStatus == MESH_ACK_OK)
                             : (meshHasParent() && g_lastTxStatus == 1);
  if (confirmed) g_unconfirmedWakes = 0;
  else if (g_unconfirmedWakes < 250) g_unconfirmedWakes++;

  goToSleep(ch);  // never returns
```

- [ ] **Step 4: Compile-verify the full change**

Run: `arduino-cli compile --fqbn esp32:esp32:esp32c3 firmware/edge_node_esp32_c3`
Expected: compiles with no errors, no unused-function warnings for `meshHandleAck`/`meshAckResult`/`MESH_ACK_OK`/`MESH_ACK_REJECTED` anymore (they're all referenced now).

- [ ] **Step 5: Commit**

```bash
git add firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino
git commit -m "$(cat <<'EOF'
feat: wire app-level mesh ack into edge_node_esp32_c3's wake cycle

After the existing L2 send-confirm succeeds, wait up to
MESH_APP_ACK_WAIT_MS for the Pi's real accept/reject before deciding
whether this cycle actually delivered -- distinguishes "nobody heard
me" from "somebody heard me but the Pi rejected it" for the first
time, logged distinctly on the serial console. Retry/rediscovery path
is unchanged (still L2-only), by design -- see the design spec.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Mirror Task 2 in `edge_node_esp32.ino` (WROOM variant)

**Files:**
- Modify: `firmware/edge_node_esp32/edge_node_esp32.ino:62-83` (`onDataRecv`), the equivalent `runSleepyCycle()` section (same structure as Task 2, slightly different line numbers in this file — locate by the same `bool delivered = sendWithConfirm(&r, deadline);` line).

**Interfaces:**
- Consumes: same as Task 2 (this file shares `mesh_node.h`/`mesh_config.h` with the C3 variant).

- [ ] **Step 1: Add the same `MeshAck` dispatch branch to this file's `onDataRecv`**

Apply the exact same one-line addition as Task 2 Step 1 (`else if (len == (int)sizeof(MeshAck)) { meshHandleAck(data, len); }`) to this file's `onDataRecv` (currently lines 62-83, byte-for-byte identical structure to the C3 variant's).

- [ ] **Step 2: Compile-verify**

Run: `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/edge_node_esp32`
Expected: compiles with no errors.

- [ ] **Step 3: Apply the same `runSleepyCycle()` change as Task 2 Step 3**

Locate this file's `runSleepyCycle()` — it has the identical `bool delivered = sendWithConfirm(&r, deadline);` ... `goToSleep(ch);` structure as `edge_node_esp32_c3.ino` (confirmed identical during planning). Apply the exact same diff as Task 2 Step 3: insert the `appAckStatus` wait block right after the `delivered`/`Serial.printf` lines, and replace the final `bool confirmed = ...` line the same way.

- [ ] **Step 4: Compile-verify the full change**

Run: `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/edge_node_esp32`
Expected: compiles with no errors.

- [ ] **Step 5: Commit**

```bash
git add firmware/edge_node_esp32/edge_node_esp32.ino
git commit -m "$(cat <<'EOF'
feat: wire app-level mesh ack into edge_node_esp32's wake cycle

Mirrors the edge_node_esp32_c3 change (same commit earlier this plan)
-- this file's onDataRecv/runSleepyCycle are structurally identical.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Mirror Task 2 in `fake_edge_node_esp32_c3.ino` (bench simulator)

**Files:**
- Modify: `firmware/fake_edge_node_esp32_c3/fake_edge_node_esp32_c3.ino:127-149` (`onDataRecv`), `:232-258` (the `runSleepyCycle()` delivery/confirm section)

**Interfaces:**
- Consumes: same as Task 2. This sketch exists specifically to mirror production edge-node mesh behavior for bench testing without real sensors (see `docs/superpowers/plans/2026-09-12-onboarding-bench-bugfixes.md` for why keeping it in sync matters — it went stale once already this project and caused confusion during bench testing).

- [ ] **Step 1: Add the same `MeshAck` dispatch branch**

In `firmware/fake_edge_node_esp32_c3/fake_edge_node_esp32_c3.ino`, `onDataRecv` (lines 127-149) has the identical shape to Task 2 Step 1's target. Apply the same one-line addition:

```cpp
  } else if (len == MESH_PACKET_LEN) {
    // Some child picked us as its parent — relay its packet toward the bridge.
    meshRelayData(info->src_addr, data, len);
  } else if (len == (int)sizeof(MeshAck)) {
    meshHandleAck(data, len);
  }
}
```

- [ ] **Step 2: Compile-verify**

Run: `arduino-cli compile --fqbn esp32:esp32:esp32c3 firmware/fake_edge_node_esp32_c3`
Expected: compiles with no errors.

- [ ] **Step 3: Apply the same delivery/confirm change**

The current block (lines 232-258) reads:

```cpp
  bool delivered = sendWithConfirm(&r, deadline);
  Serial.printf("[wake] delivered=%d hasParent=%d\n", delivered, meshHasParent());

  if (!delivered && millis() < deadline) {
    if (meshHasParent()) meshDropParent("wake tx unconfirmed");
    meshRequeueLastReading();

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

  bool confirmed = delivered || (meshHasParent() && g_lastTxStatus == 1);
  if (confirmed) g_unconfirmedWakes = 0;
  else if (g_unconfirmedWakes < 250) g_unconfirmedWakes++;

  goToSleep(ch);  // never returns
```

Change it to:

```cpp
  bool delivered = sendWithConfirm(&r, deadline);
  Serial.printf("[wake] delivered=%d hasParent=%d\n", delivered, meshHasParent());

  // Only the direct-send path (not the requeue/rediscovery retry below) waits
  // for the app-level ack: this is specifically the "L2 delivered, but did
  // the Pi actually accept it" question, and the retry path below already
  // has its own, unchanged, L2-only confirm dance for "is there a route at
  // all" -- a different failure class with a different existing answer.
  int appAckStatus = -1;
  if (delivered) {
    uint32_t ackWaitStart = millis();
    while (meshAckResult() == -1 &&
           millis() - ackWaitStart < MESH_APP_ACK_WAIT_MS &&
           millis() < deadline) {
      delay(5);
    }
    appAckStatus = meshAckResult();
    if (appAckStatus == MESH_ACK_OK) {
      Serial.println("[wake] Pi confirmed reading accepted");
    } else if (appAckStatus == MESH_ACK_REJECTED) {
      Serial.println("[wake] Pi rejected reading — check AppKey");
    } else {
      Serial.println("[wake] no app-level ack within wait window");
    }
  }

  if (!delivered && millis() < deadline) {
    if (meshHasParent()) meshDropParent("wake tx unconfirmed");
    meshRequeueLastReading();

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

  // Direct-send path: only a real app-level MESH_ACK_OK counts as confirmed
  // now. Retry path: unchanged, L2-only -- it never got an app-ack wait.
  bool confirmed = delivered ? (appAckStatus == MESH_ACK_OK)
                             : (meshHasParent() && g_lastTxStatus == 1);
  if (confirmed) g_unconfirmedWakes = 0;
  else if (g_unconfirmedWakes < 250) g_unconfirmedWakes++;

  goToSleep(ch);  // never returns
```

This is byte-for-byte the same transformation as Task 2 Step 3, applied to this file's identical structure (verified during planning: this file's lines 232-261 match `edge_node_esp32_c3.ino`'s equivalent section exactly, including the trailing `goToSleep(ch);` and closing brace).

- [ ] **Step 4: Compile-verify the full change**

Run: `arduino-cli compile --fqbn esp32:esp32:esp32c3 firmware/fake_edge_node_esp32_c3`
Expected: compiles with no errors.

- [ ] **Step 5: Commit**

```bash
git add firmware/fake_edge_node_esp32_c3/fake_edge_node_esp32_c3.ino
git commit -m "$(cat <<'EOF'
feat: wire app-level mesh ack into the fake sensor bench firmware

Keeps the bench simulator's mesh behavior matching production
(edge_node_esp32_c3.ino) exactly, same reason this file was
resynced from scratch in the 2026-09-12 session.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Bridge originates the ack over UART

**Files:**
- Modify: `firmware/bridge_esp32/bridge_esp32.ino:57-89` (`handleUartLine`)

**Interfaces:**
- Consumes: `meshBuildAck()`, `MeshAck`, `MESH_ACK_OK`, `MESH_ACK_REJECTED`, `MESH_BCAST` (all from Task 1); the existing `hexToBytes()` helper already in this file.
- Produces (consumed by Task 6's Pi-side tests indirectly, via the UART line contract): accepts a new incoming UART line shape `{"type":"ack","mac":"<12 hex>","seq":<int>,"ok":true|false}`.

- [ ] **Step 1: Add the new UART command branch**

In `firmware/bridge_esp32/bridge_esp32.ino`, `handleUartLine()` currently ends with:

```cpp
  // {"type":"provision","mac":"<12 hex>","blob":"<66 hex>"}
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
```

Add a new branch right after the `provision` block, before the closing `}`:

```cpp
  // {"type":"provision","mac":"<12 hex>","blob":"<66 hex>"}
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
    return;
  }
  // {"type":"ack","mac":"<12 hex>","seq":<int>,"ok":true|false}
  if (strstr(line, "\"ack\"")) {
    const char* m = strstr(line, "\"mac\":\"");
    const char* s = strstr(line, "\"seq\":");
    const char* o = strstr(line, "\"ok\":");
    uint8_t mac[6];
    if (!m || !s || !o || !hexToBytes(m + 7, mac, 6)) return;
    uint16_t seq = (uint16_t)atoi(s + 6);
    bool ok = (strncmp(o + 5, "true", 4) == 0);
    uint8_t ackPkt[sizeof(MeshAck)];
    if (meshBuildAck(ackPkt, mac, seq, ok ? MESH_ACK_OK : MESH_ACK_REJECTED)) {
      esp_now_send(MESH_BCAST, ackPkt, sizeof(ackPkt));
      Serial.printf("[bridge] ack broadcast: seq=%u ok=%d\n", seq, ok);
    }
  }
}
```

(Note the added `return;` at the end of the `provision` block — it was previously the last statement in the function so falling through was harmless, but it's no longer the last block now that `ack` follows it.)

- [ ] **Step 2: Compile-verify**

Run: `arduino-cli compile --fqbn esp32:esp32:esp32c3 firmware/bridge_esp32`
Expected: compiles with no errors (the bridge board is an ESP32-C3 despite the folder name — confirmed earlier this project via `esptool`).

- [ ] **Step 3: Commit**

```bash
git add firmware/bridge_esp32/bridge_esp32.ino
git commit -m "$(cat <<'EOF'
feat: bridge originates MeshAck from a new UART "ack" command

The Pi tells the bridge whether it accepted or rejected a reading via
one new UART line; the bridge signs and broadcasts a MeshAck built
from meshBuildAck() (Task 1 of this plan). The bridge never relays
someone else's ack -- it's always the origin, since it's the only
thing with a link to the Pi.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Pi sends the ack command to the bridge

**Files:**
- Modify: `pi/scripts/serial_bridge.py:257-300` (`handle_frame`), `:340-345` area (new `send_ack` function, next to `send_netkey`/`send_provision`), `:384-385` (`handle_line`'s `frame` dispatch)
- Test: `pi/tests/test_serial_bridge_mesh.py`

**Interfaces:**
- Produces: `send_ack(ser, mac: str, seq: int, ok: bool) -> None`.
- Consumes: existing `_send_command(ser, payload: dict)` helper (already in this file, used by `send_netkey`/`send_provision`).
- Changes: `handle_frame`'s signature gains a 4th parameter `ser=None` (backward-compatible default — every existing test calling `handle_frame(client, msg, state)` with 3 positional args keeps working unchanged); `handle_line`'s `frame` dispatch passes its own already-in-scope `ser` through.

- [ ] **Step 1: Write the failing tests**

Add to `pi/tests/test_serial_bridge_mesh.py`, after the existing `test_send_netkey_writes_one_json_line` test:

```python
def test_send_ack_writes_one_json_line():
    written = []
    sb.send_ack(type('S', (), {'write': lambda _s, b: written.append(b)})(), MAC_S, 7, True)
    assert written[0].endswith(b'\n')
    assert b'"ack"' in written[0]
    assert b'"seq":7' in written[0]
    assert b'"ok":true' in written[0]


def test_a_valid_frame_sends_a_positive_ack(state, monkeypatch):
    acked = []
    monkeypatch.setattr(sb, 'send_ack', lambda *a, **kw: acked.append((a, kw)))
    sentinel_ser = object()
    c = FakeClient()
    sb.handle_frame(c, _frame(), state, sentinel_ser)
    assert acked == [((sentinel_ser, MAC_S, 1), {'ok': True})]


def test_an_auth_failure_sends_a_negative_ack(state, monkeypatch):
    acked = []
    monkeypatch.setattr(sb, 'send_ack', lambda *a, **kw: acked.append((a, kw)))
    f = _frame()
    raw = bytearray(bytes.fromhex(f['data']))
    raw[30] ^= 0x01
    sentinel_ser = object()
    c = FakeClient()
    sb.handle_frame(c, {'type': 'frame', 'data': bytes(raw).hex()}, state, sentinel_ser)
    assert acked == [((sentinel_ser, MAC_S, 1), {'ok': False})]


def test_handle_frame_without_a_serial_connection_does_not_crash(state):
    # Backward compatibility: every pre-existing call site/test omits ser.
    c = FakeClient()
    sb.handle_frame(c, _frame(), state)
    topics = [t for t, _, _ in c.published]
    assert any('temperature' in t for t in topics)


def test_unenrolled_and_replay_drops_do_not_send_an_ack(state, monkeypatch):
    acked = []
    monkeypatch.setattr(sb, 'send_ack', lambda *a, **kw: acked.append((a, kw)))
    sentinel_ser = object()
    c = FakeClient()
    other = mc.seal_packet(bytes(16), NET, bytes.fromhex('AABBCCDDEEFF'),
                           seq=1, boot_count=1, flags=0, rank=1, ttl=4,
                           body=mp.pack_body(1, 1, 1, 0, PARENT, 0))
    sb.handle_frame(c, {'type': 'frame', 'data': other.hex()}, state, sentinel_ser)
    sb.handle_frame(c, _frame(), state, sentinel_ser)   # first: accepted
    acked.clear()
    sb.handle_frame(c, _frame(), state, sentinel_ser)   # same seq again: replay
    assert acked == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pi/tests/test_serial_bridge_mesh.py -v -k "ack"`
Expected: all 5 new tests FAIL — `send_ack` doesn't exist yet (`AttributeError`), and `handle_frame` doesn't accept a 4th positional argument yet (`TypeError`).

- [ ] **Step 3: Add `send_ack`**

In `pi/scripts/serial_bridge.py`, the current block (lines 340-346) reads:

```python
def send_netkey(ser, net_key: bytes) -> None:
    _send_command(ser, {'type': 'netkey', 'key': net_key.hex()})


def send_provision(ser, mac: str, blob: bytes) -> None:
    _send_command(ser, {'type': 'provision', 'mac': node_store.normalise_mac(mac),
                        'blob': blob.hex()})
```

Add a new function right after `send_provision`:

```python
def send_netkey(ser, net_key: bytes) -> None:
    _send_command(ser, {'type': 'netkey', 'key': net_key.hex()})


def send_provision(ser, mac: str, blob: bytes) -> None:
    _send_command(ser, {'type': 'provision', 'mac': node_store.normalise_mac(mac),
                        'blob': blob.hex()})


def send_ack(ser, mac: str, seq: int, ok: bool) -> None:
    _send_command(ser, {'type': 'ack', 'mac': mac, 'seq': seq, 'ok': ok})
```

- [ ] **Step 4: Wire it into `handle_frame` and thread `ser` through from `handle_line`**

In `pi/scripts/serial_bridge.py`, `handle_frame`'s signature and its two silent-failure/success points (already carrying the 2026-09-15 `log_security_event` calls) currently read:

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

    for metric, (group, topic_metric) in _METRICS.items():
        client.publish(_reading_topic(node.zone, group, topic_metric),
                       f'{float(getattr(body, metric)):.1f}', retain=True)
    if body.battery_mv:
        client.publish(_battery_topic(mac), f'{body.battery_mv / 1000:.2f}', retain=True)
    client.publish(_status_topic(mac), 'online', retain=True)
```

Change the signature and the two flagged branches (leave the `malformed frame` branch, the `_METRICS` publish loop, and everything below `client.publish(_status_topic(mac), 'online', retain=True)` untouched):

```python
def handle_frame(client, msg: dict, state: dict, ser=None) -> None:
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
        if ser is not None:
            send_ack(ser, mac, header.seq, ok=False)
        print(f'[mesh] {mac} failed authentication: {exc}', flush=True)
        return

    for metric, (group, topic_metric) in _METRICS.items():
        client.publish(_reading_topic(node.zone, group, topic_metric),
                       f'{float(getattr(body, metric)):.1f}', retain=True)
    if body.battery_mv:
        client.publish(_battery_topic(mac), f'{body.battery_mv / 1000:.2f}', retain=True)
    client.publish(_status_topic(mac), 'online', retain=True)
    if ser is not None:
        send_ack(ser, mac, header.seq, ok=True)
```

Note `send_ack` is only reachable from `mesh_unenrolled_frame`/`mesh_replay_dropped` paths *not at all* — this matches the spec's non-goal ("unenrolled/replay-drop frames do not get an ack") exactly, since those two `return` statements are above where `send_ack` was added.

Now update the one call site. In the same file, `handle_line` (currently around line 384-385) reads:

```python
        elif msg_type == 'frame':
            handle_frame(client, msg, state)
```

Change it to:

```python
        elif msg_type == 'frame':
            handle_frame(client, msg, state, ser)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest pi/tests/test_serial_bridge_mesh.py -v`
Expected: all tests PASS, including the 5 new ones.

- [ ] **Step 6: Run the full Pi test suite**

Run: `python -m pytest pi/tests/ -v`
Expected: all tests pass except the pre-existing, unrelated Windows-only
`test_nodes.py::test_store_is_written_0600_because_it_holds_every_app_key`
failure (POSIX file-permission test, fails on Windows/NTFS, passes on Linux
CI — confirmed pre-existing via `git stash` during the 2026-09-15 session,
nothing to do with this change).

- [ ] **Step 7: Commit**

```bash
git add pi/scripts/serial_bridge.py pi/tests/test_serial_bridge_mesh.py
git commit -m "$(cat <<'EOF'
feat: Pi sends accept/reject ack to the bridge for every mesh frame

handle_frame() gains an optional `ser` parameter (default None, so
every existing call site/test is unaffected) and calls the new
send_ack() on both outcomes it already logs to security_log: a
successful decrypt (ok=True) and an auth failure from an already-
enrolled node (ok=False). Unenrolled-MAC and replay-drop frames
still get no ack, matching the design spec's scope -- an unenrolled
node has no NetKey to verify one with, and a replay is usually a
duplicate of an already-acked delivery.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes (for the implementer, not a step to execute)

- **Spec coverage:** every §Architecture subsection of the design spec has a task — wire format (Task 1), flood-relay mechanics (Task 1 `meshHandleAck`), Pi/bridge changes (Tasks 5-6), sensor-side timing (Task 2, mirrored in Tasks 3-4). The §Duplicate-arrival handling idempotency requirement is implemented as the plain-overwrite in Task 1 Step 4's `meshHandleAck` — no counter, no toggle, exactly as required.
- **Not covered by this plan, deliberately:** real hardware bench verification (three serial-console outcomes actually observed against a live Pi/bridge) — not a firmware/Pi code task, do this after Task 6, by hand, the same way every other mesh protocol change in this project has been bench-verified (2026-08-16 relay test, 2026-09-12 enrollment bugfixes).
- **Task ordering matters:** Task 1 must land before Tasks 2-6 (all consume its new functions/constants). Tasks 2, 3, 4 are independent of each other and of Task 5 — could run in parallel if using subagent-driven-development. Task 6 is independent of Tasks 2-5 (Pi-side only) but conceptually pairs with Task 5 (the UART contract between them) — recommend doing Task 5 before Task 6 so the wire contract is settled first, though nothing technically blocks reversing them.
- Do not touch `app/` — this plan is entirely firmware + Pi, matching the spec's explicit non-goal.
