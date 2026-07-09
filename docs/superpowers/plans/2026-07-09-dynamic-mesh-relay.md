# Dynamic Multi-Hop Mesh Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the star-topology ESP-NOW firmware (every edge node hardcodes the bridge MAC) with a dynamic multi-hop mesh: rank/hop-count routing with strict-rank loop safety, trickle-style adaptive beaconing, PMK/LMK-encrypted unicast data relaying, local buffering when isolated, and bridge-side offline-status detection.

**Architecture:** A new shared Arduino library `GreenhouseMesh` (two headers: `mesh_config.h` for keys/trusted-node list/tuning constants, `mesh_node.h` for wire structs and the routing/trickle/dedup/buffer core) is included by all three sketches. Edge nodes discover neighbors via cleartext broadcast beacons, pick a parent by lowest advertised rank (RSSI tiebreak, strictly-lower-rank rule), unicast encrypted `MeshDataPacket`s to it, and relay children's packets upward. The bridge beacons continuously at rank 0, looks up zones by `origin_mac` (not the immediate sender), publishes readings with `retain=true`, and publishes `offline` status when a node goes silent.

**Tech Stack:** C++ / Arduino (ESP32 Arduino Core v3.x), ESP-NOW (`esp_now.h`, PMK/LMK encrypted peers), PubSubClient (bridge only). No Flutter/Dart or Pi-side code changes.

**Reference spec:** `docs/superpowers/specs/2026-07-09-dynamic-mesh-relay-design.md`

> **⚠ TDD does not apply to this plan.** This is Arduino firmware, not the Flutter app or Pi Python — the project has no automated firmware test harness and this feature deliberately does not add one (spec Non-goals). The repo's usual TDD convention applies only to the Dart/Python code. Per-task verification here is an Arduino IDE compile ("Verify") with the correct board selected; end-to-end verification is the manual serial-monitor bench plan in Task 5, taken verbatim from the spec's Testing section (consistent with `docs/ESP_NOW_BRIDGE_PROGRESS.md`'s established practice).

## Global Constraints

- Tuning constants exactly as the spec fixes them: `BEACON_INTERVAL_MIN` = 2 s, `BEACON_INTERVAL_MAX` = 60 s, parent timeout = 3× the parent's last-advertised beacon interval, `MESH_MAX_TTL` = 4, de-dup cache = 32 entries, local reading buffer = 10 most-recent (oldest dropped, not persisted), `OFFLINE_AFTER` = 3× the expected report interval. Bench testing (Task 5) may adjust them afterward, per the spec's consistency note.
- `SensorPacket` (`temperature`, `humidity`, `soil_moisture` — three floats) stays byte-identical; it is wrapped inside `MeshDataPacket`, never modified.
- Beacons are ESP-NOW broadcast and therefore always cleartext (platform limitation); sensor data is unicast and encrypted via one network-wide PMK+LMK pair compiled into all firmware (documented thesis-scope limitation — no per-pair keys).
- A node ignores beacons from any MAC not in `TRUSTED_NODES[]`; untrusted senders can never become a parent, and untrusted data unicast fails to decrypt (no peer relationship).
- ESP32 Arduino Core **v3.x** callback signatures throughout: `onDataRecv(const esp_now_recv_info_t*, const uint8_t*, int)` and `onDataSent(const wifi_tx_info_t*, esp_now_send_status_t)` — see `docs/ESP_NOW_BRIDGE_PROGRESS.md` issues #1–2 for why.
- ESP-NOW allows at most 7 encrypted peers by default: keep `TRUSTED_NODES[]` at ≤ 8 entries (each node registers every entry except itself). Fine for this project's actual node count (3).
- The wire format is **not backward compatible** with the old bare-`SensorPacket` protocol. All three devices are reflashed together in Task 5 step 1; there is no mixed-fleet mode.
- No changes to anything under `app/` or `pi/`. No automated tests added.
- Deep sleep is **not** implemented (spec Non-goals): `window_duration_ms` is carried in every beacon and propagated hop-by-hop, but nothing schedules against it yet.

---

## File Structure

| File | Responsibility |
|---|---|
| `firmware/libraries/GreenhouseMesh/library.properties` (new) | Minimal Arduino library manifest |
| `firmware/libraries/GreenhouseMesh/mesh_config.h` (new) | Network-wide PMK/LMK, `TRUSTED_NODES[]` (supersedes the bridge's `ZONES[]`), all tuning constants |
| `firmware/libraries/GreenhouseMesh/mesh_node.h` (new) | Wire structs (`MeshBeacon`, `MeshDataPacket`, `SensorPacket`), trust helpers, encrypted-peer setup, beacon handling / parent selection, trickle timer, de-dup cache, relay forwarding, local buffering |
| `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` (rewrite) | C3 edge node: sensors + mesh integration, non-blocking loop |
| `firmware/edge_node_esp32/edge_node_esp32.ino` (rewrite) | WROOM edge node: same behavior, its own pins |
| `firmware/bridge_esp32/bridge_esp32.ino` (rewrite) | Rank-0 beacon, origin-based zone lookup, `retain=true`, offline-status publishing |

**Shared header vs. per-sketch duplication — the call:** shared library, not duplication. The existing sketches duplicate only the 3-line `SensorPacket` struct; the mesh core is ~250 lines of stateful routing logic, and triplicating it across three sketches is a guaranteed-drift hazard (one fixed sketch, two stale ones). The spec itself mandates a single-source-of-truth shared header for config. The one wrinkle: Arduino IDE copies each sketch to a temp build folder before compiling, so `#include "../mesh_config.h"` parent-relative includes silently break — the only reliable sharing mechanism is the sketchbook `libraries/` folder. So the shared code lives in-repo at `firmware/libraries/GreenhouseMesh/` (one directory deeper than the spec's `firmware/mesh_config.h` — same intent, real-toolchain path), linked into the Arduino sketchbook once via a directory junction (Task 1 step 2). Both headers are header-only with `static` state — each `.ino` is a single translation unit, so no `.cpp`/linking complexity.

---

### Task 1: `GreenhouseMesh` library scaffolding + `mesh_config.h`

**Files:**
- Create: `firmware/libraries/GreenhouseMesh/library.properties`
- Create: `firmware/libraries/GreenhouseMesh/mesh_config.h`

**Interfaces:**
- Consumes: nothing (first task).
- Produces (used by Tasks 2–4): macros `MESH_MAGIC` (0x47), `MESH_RANK_UNROUTED` (255), `MESH_MAX_TTL` (4), `MESH_BEACON_INTERVAL_MIN_MS` (2000), `MESH_BEACON_INTERVAL_MAX_MS` (60000), `MESH_BRIDGE_BEACON_INTERVAL_MS` (2000), `MESH_PARENT_TIMEOUT_FACTOR` (3), `MESH_WINDOW_DURATION_MS` (3000), `MESH_DEDUP_CACHE_SIZE` (32), `MESH_DATA_BUFFER_SIZE` (10), `MESH_OFFLINE_AFTER` (3), `MESH_EXPECTED_REPORT_INTERVAL_MS` (5000), `MESH_RESCAN_AFTER_MS` (60000); arrays `MESH_PMK[16]`, `MESH_LMK[16]`; `struct TrustedNode { uint8_t mac[6]; const char* zone; }`; `TRUSTED_NODES[]`; `TRUSTED_NODE_COUNT`.

- [ ] **Step 1: Create the library manifest**

Create `firmware/libraries/GreenhouseMesh/library.properties`:

```properties
name=GreenhouseMesh
version=1.0.0
author=vtsitotas
maintainer=vtsitotas
sentence=Shared mesh-relay config and routing core for the greenhouse ESP-NOW nodes.
paragraph=Single source of truth for PMK/LMK keys, the trusted-node list, tuning constants, and the rank/trickle/relay logic used by the bridge and both edge node sketches.
category=Communication
url=https://github.com/vtsitotas/GreenHouse
architectures=esp32
```

- [ ] **Step 2: Create `mesh_config.h`**

Create `firmware/libraries/GreenhouseMesh/mesh_config.h`:

```cpp
#pragma once
// ── GreenhouseMesh: network-wide configuration ────────────────────────────────
// Single source of truth for every node (bridge + both edge variants).
// Supersedes the bridge's old ZONES[] array. Adding a node = add its MAC/zone
// here and reflash the fleet (same process as ZONES[] before — spec Non-goals).

#include <stdint.h>

// ── Protocol ──────────────────────────────────────────────────────────────────
#define MESH_MAGIC          0x47   // 'G' — version/sanity marker on every packet
#define MESH_RANK_UNROUTED  255    // sentinel: node has no valid parent
#define MESH_MAX_TTL        4      // hard hop backstop (loops are structurally
                                   // prevented by the strict-rank rule; this is
                                   // defense in depth only)

// ── Timing (spec-fixed starting values; Task 5 bench may tune) ────────────────
#define MESH_BEACON_INTERVAL_MIN_MS    2000UL   // trickle floor — reset target
#define MESH_BEACON_INTERVAL_MAX_MS    60000UL  // trickle ceiling
#define MESH_BRIDGE_BEACON_INTERVAL_MS 2000UL   // bridge is mains-powered: fixed,
                                                // no trickle backoff needed
#define MESH_PARENT_TIMEOUT_FACTOR     3        // parent lost after 3x its
                                                // last-advertised beacon interval
#define MESH_WINDOW_DURATION_MS        3000UL   // shared wake window, bridge-
                                                // originated. Forward-compat for
                                                // deep sleep — carried, unused today.
#define MESH_RESCAN_AFTER_MS           60000UL  // unrouted this long → re-scan the
                                                // router channel (router may have
                                                // moved channels)

// ── Buffers ───────────────────────────────────────────────────────────────────
#define MESH_DEDUP_CACHE_SIZE  32   // (origin_mac, seq) ring — drops route-flap dupes
#define MESH_DATA_BUFFER_SIZE  10   // own readings buffered while isolated
                                    // (most-recent 10, oldest dropped, RAM only)

// ── Bridge offline detection ──────────────────────────────────────────────────
#define MESH_OFFLINE_AFTER               3       // x expected report interval
#define MESH_EXPECTED_REPORT_INTERVAL_MS 5000UL  // matches SEND_INTERVAL_MS on edges

// ── Keys (16 bytes each, network-wide — spec Non-goals: no per-pair keys) ─────
// Shared-key model: defends against a nearby stranger device injecting/reading
// data; does NOT defend against key extraction from a captured node.
static const uint8_t MESH_PMK[16] =
  { 'g','h','-','m','e','s','h','-','p','m','k','-','0','0','0','1' };
static const uint8_t MESH_LMK[16] =
  { 'g','h','-','m','e','s','h','-','l','m','k','-','0','0','0','1' };

// ── Trusted nodes ─────────────────────────────────────────────────────────────
// Every real device in the network, bridge included (zone = nullptr for the
// bridge). A MAC not in this list is ignored as a routing candidate and has no
// encrypted-peer relationship, so its data unicasts can't even decrypt.
// LIMIT: keep at most 8 entries — ESP-NOW allows 7 encrypted peers per node
// (each node registers every entry except itself).
struct TrustedNode {
  uint8_t     mac[6];
  const char* zone;   // MQTT zone name, or nullptr for the bridge
};

static const TrustedNode TRUSTED_NODES[] = {
  { { 0x20, 0x6E, 0xF1, 0x6C, 0x6B, 0x50 }, nullptr },  // bridge (ESP32-C3)
  { { 0x20, 0x6E, 0xF1, 0x6C, 0xA1, 0xB0 }, "zone1" },  // ESP32-C3 edge node
  { { 0x88, 0xF1, 0x55, 0x31, 0x45, 0x64 }, "zone2" },  // ESP32 WROOM-32 edge node
};
static const int TRUSTED_NODE_COUNT = sizeof(TRUSTED_NODES) / sizeof(TRUSTED_NODES[0]);
```

(MAC provenance: bridge MAC from `bridgeMac[]` at `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:20`; the two edge MACs/zones from the bridge's `ZONES[]` at `firmware/bridge_esp32/bridge_esp32.ino:20-23`. Ignore the stale `20:6E:F1:6C:A1:B0` "Bridge MAC" in `docs/ESP_NOW_BRIDGE_PROGRESS.md` — the current firmware is authoritative.)

- [ ] **Step 3: Link the library into the Arduino sketchbook (one-time, per dev machine)**

Arduino IDE only resolves shared headers through the sketchbook `libraries/` folder, so create a directory junction from the default sketchbook into the repo (keeps the repo as the single editable copy; no admin rights needed for junctions):

```powershell
New-Item -ItemType Junction `
  -Path "$env:USERPROFILE\Documents\Arduino\libraries\GreenhouseMesh" `
  -Target "C:\Users\billy\Desktop\diplomatikh\firmware\libraries\GreenhouseMesh"
```

Expected: a junction listing printed; `Get-ChildItem "$env:USERPROFILE\Documents\Arduino\libraries\GreenhouseMesh"` shows `library.properties` and `mesh_config.h`. (If the sketchbook location was customized in Arduino IDE preferences, substitute that path.)

- [ ] **Step 4: Manual verification — the header compiles**

In Arduino IDE (restart it if it was open, so it picks up the new library): File → New Sketch, contents:

```cpp
#include <mesh_config.h>
void setup() { Serial.begin(115200); Serial.println(TRUSTED_NODE_COUNT); }
void loop() {}
```

Select board **ESP32C3 Dev Module** (Tools → Board → esp32) and click Verify.
Expected: `Done compiling` with no errors. Discard the scratch sketch afterward (don't commit it).

- [ ] **Step 5: Commit**

```bash
git add firmware/libraries/GreenhouseMesh/library.properties firmware/libraries/GreenhouseMesh/mesh_config.h
git commit -m "feat: add shared GreenhouseMesh config header (PMK/LMK, trusted nodes, tuning)"
```

---

### Task 2: `mesh_node.h` — wire structs + routing/trickle/relay core

**Files:**
- Create: `firmware/libraries/GreenhouseMesh/mesh_node.h`

**Interfaces:**
- Consumes: everything `mesh_config.h` produces (Task 1).
- Produces (exact signatures Tasks 3–4 rely on):
  - Structs: `SensorPacket { float temperature; float humidity; float soil_moisture; }` (unchanged from today), `MeshBeacon` (18 bytes packed), `MeshDataPacket` (23 bytes packed) — per the spec's Packet Formats section, byte for byte.
  - `void meshInit(uint8_t channel)` — caches own MAC, sets PMK, registers broadcast + all encrypted peers. Call **after** `esp_now_init()`. Pass `0` = "follow the radio's current channel" (lets a later channel re-scan work without re-registering peers).
  - `int meshTrustedIndex(const uint8_t* mac)` — index into `TRUSTED_NODES[]`, or −1.
  - `void meshFormatMac(const uint8_t* mac, char* out)` — 12 uppercase hex chars + NUL into a 13-byte buffer (same format the MQTT topics already use).
  - `void meshSendBeaconNow(uint8_t rank, uint32_t advertisedIntervalMs)` — broadcast one beacon (bridge uses this directly with rank 0).
  - `void meshBeaconTick(uint32_t now)` — edge trickle beacon: sends when due, doubles the interval up to the cap.
  - `void meshTrickleReset(void)` — interval back to `MESH_BEACON_INTERVAL_MIN_MS`.
  - `void meshHandleBeacon(const uint8_t* srcMac, const MeshBeacon* b, int rssi, uint32_t now)` — trust filter, parent selection (strict-rank + RSSI tiebreak), parent refresh/loss on rank change.
  - `void meshCheckParentTimeout(uint32_t now)` — drops the parent after `MESH_PARENT_TIMEOUT_FACTOR` × its advertised interval of silence.
  - `bool meshDedupSeen(const uint8_t* originMac, uint16_t seq)` — checks *and records* in the ring cache; also used by the bridge.
  - `void meshSendReading(const SensorPacket* payload)` — wraps + unicasts own reading to the parent, or buffers it while unrouted; flushes the buffer first when routed.
  - `void meshRelayData(const uint8_t* srcMac, const uint8_t* data, int len)` — TTL/dedup checks, then forwards a child's packet to own parent with `ttl−1`.
  - `void meshNotifyTxStatus(bool ok)` — call from the send callback; 3 consecutive unicast failures drop the parent (backstop for a parent that died at a long trickle interval).
  - `bool meshHasParent(void)`.
  - Global `uint8_t meshSelfMac[6]` (read-only use by sketches for logging).

- [ ] **Step 1: Write `mesh_node.h`**

Create `firmware/libraries/GreenhouseMesh/mesh_node.h`:

```cpp
#pragma once
// ── GreenhouseMesh: routing / trickle / relay core ────────────────────────────
// Header-only on purpose: every Arduino sketch is one translation unit, so the
// static state below is private per device and there is no library .cpp to link.
// Design: RPL-inspired strict rank ordering (a parent's advertised rank must be
// STRICTLY below our own) makes routing loops structurally impossible; TTL is a
// cheap backstop only. Trickle-style beacon backoff makes airtime cost
// proportional to instability, not wall-clock time.

#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <string.h>
#include "mesh_config.h"

// ── Wire formats (spec: Packet Formats — do not reorder fields) ──────────────
typedef struct {
  float temperature;
  float humidity;
  float soil_moisture;  // 0–100 %
} SensorPacket;

// Broadcast, cleartext (broadcast frames cannot be ESP-NOW-encrypted).
// Neighbor discovery + rank advertisement only — never sensor data.
typedef struct __attribute__((packed)) {
  uint8_t  magic;               // MESH_MAGIC
  uint8_t  mac[6];              // sender's own MAC (informational; trust checks
                                // use the frame's src_addr)
  uint8_t  rank;                // sender's current rank (255 = unrouted)
  uint16_t seq;                 // monotonic per-sender counter
  uint32_t beacon_interval_ms;  // gap until sender's NEXT beacon (children size
                                // their parent-timeout from this)
  uint32_t window_duration_ms;  // bridge-originated, propagated hop-by-hop.
                                // Deep-sleep forward-compat: carried, unused.
} MeshBeacon;                   // 18 bytes

// Unicast to the chosen parent, ESP-NOW encrypted (PMK/LMK).
typedef struct __attribute__((packed)) {
  uint8_t      magic;           // MESH_MAGIC
  uint8_t      origin_mac[6];   // node the reading is FROM (not the relay hop)
  uint8_t      origin_rank;     // origin's rank at send time (diagnostics only)
  uint8_t      ttl;             // decremented per hop, dropped at 0
  uint16_t     seq;             // per-origin monotonic counter, for de-dup
  SensorPacket payload;         // unchanged existing struct
} MeshDataPacket;               // 23 bytes — length disambiguates from MeshBeacon

static const uint8_t MESH_BCAST[6] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };

// ── State ─────────────────────────────────────────────────────────────────────
static uint8_t  meshSelfMac[6];
static uint8_t  meshMyRank            = MESH_RANK_UNROUTED;
static int      meshParentIdx         = -1;   // index into TRUSTED_NODES, -1 = unrouted
static uint8_t  meshParentRank        = MESH_RANK_UNROUTED;
static uint32_t meshParentIntervalMs  = MESH_BEACON_INTERVAL_MIN_MS;
static uint32_t meshParentLastHeardMs = 0;
static int      meshParentRssi        = -128;

static uint32_t meshBeaconIntervalMs  = MESH_BEACON_INTERVAL_MIN_MS;
static uint32_t meshLastBeaconMs      = 0;
static uint16_t meshBeaconSeq         = 0;
static uint32_t meshWindowDurationMs  = MESH_WINDOW_DURATION_MS;

static uint32_t meshNeighborLastHeard[TRUSTED_NODE_COUNT];  // 0 = never heard

typedef struct { uint8_t mac[6]; uint16_t seq; bool used; } MeshDedupEntry;
static MeshDedupEntry meshDedup[MESH_DEDUP_CACHE_SIZE];
static int meshDedupNext = 0;

static MeshDataPacket meshBuf[MESH_DATA_BUFFER_SIZE];  // own readings while isolated
static int meshBufCount = 0;
static int meshBufHead  = 0;   // oldest entry
static uint16_t meshDataSeq = 0;
static int meshTxFailCount  = 0;

// ── Trust helpers ─────────────────────────────────────────────────────────────
static bool meshMacEqual(const uint8_t* a, const uint8_t* b) {
  return memcmp(a, b, 6) == 0;
}

static int meshTrustedIndex(const uint8_t* mac) {
  for (int i = 0; i < TRUSTED_NODE_COUNT; i++)
    if (meshMacEqual(TRUSTED_NODES[i].mac, mac)) return i;
  return -1;
}

static void meshFormatMac(const uint8_t* mac, char* out) {  // out: 13 bytes
  snprintf(out, 13, "%02X%02X%02X%02X%02X%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

// ── Init: PMK + peer registration ─────────────────────────────────────────────
// Every trusted node might dynamically become anyone's parent at runtime, so
// encrypted-peer relationships are registered network-wide up front (spec:
// Trust & Security Model). channel 0 = follow the radio's current channel.
static void meshInit(uint8_t channel) {
  WiFi.macAddress(meshSelfMac);
  memset(meshNeighborLastHeard, 0, sizeof(meshNeighborLastHeard));
  memset(meshDedup, 0, sizeof(meshDedup));

  esp_now_set_pmk((const uint8_t*)MESH_PMK);  // must precede encrypted add_peer

  esp_now_peer_info_t bcast = {};
  memcpy(bcast.peer_addr, MESH_BCAST, 6);
  bcast.channel = channel;
  bcast.encrypt = false;  // broadcast frames cannot be encrypted
  esp_now_add_peer(&bcast);

  for (int i = 0; i < TRUSTED_NODE_COUNT; i++) {
    if (meshMacEqual(TRUSTED_NODES[i].mac, meshSelfMac)) continue;  // not self
    esp_now_peer_info_t p = {};
    memcpy(p.peer_addr, TRUSTED_NODES[i].mac, 6);
    p.channel = channel;
    p.encrypt = true;
    memcpy(p.lmk, MESH_LMK, 16);
    if (esp_now_add_peer(&p) != ESP_OK)
      Serial.printf("[mesh] add_peer failed for trusted node %d\n", i);
  }
}

// ── Beacon TX (trickle) ───────────────────────────────────────────────────────
static void meshTrickleReset() {
  meshBeaconIntervalMs = MESH_BEACON_INTERVAL_MIN_MS;
}

static void meshSendBeaconNow(uint8_t rank, uint32_t advertisedIntervalMs) {
  MeshBeacon b;
  b.magic = MESH_MAGIC;
  memcpy(b.mac, meshSelfMac, 6);
  b.rank               = rank;
  b.seq                = meshBeaconSeq++;
  b.beacon_interval_ms = advertisedIntervalMs;
  b.window_duration_ms = meshWindowDurationMs;
  esp_now_send(MESH_BCAST, (const uint8_t*)&b, sizeof(b));
}

// Edge-node beacon scheduler: send when due, then double the interval (capped).
// Any topology change elsewhere calls meshTrickleReset(), so a settled network
// converges to one beacon per node per 60s while an unstable one beacons at 2s.
static void meshBeaconTick(uint32_t now) {
  if (now - meshLastBeaconMs < meshBeaconIntervalMs) return;
  meshLastBeaconMs = now;
  uint32_t next = meshBeaconIntervalMs * 2;
  if (next > MESH_BEACON_INTERVAL_MAX_MS) next = MESH_BEACON_INTERVAL_MAX_MS;
  meshSendBeaconNow(meshMyRank, next);  // advertise the NEXT gap so children
  meshBeaconIntervalMs = next;          // compute their timeout correctly
}

// ── Parent management ─────────────────────────────────────────────────────────
static void meshDropParent(const char* why) {
  Serial.printf("[mesh] parent lost (%s) — unrouted, rediscovering\n", why);
  meshParentIdx  = -1;
  meshParentRank = MESH_RANK_UNROUTED;
  meshMyRank     = MESH_RANK_UNROUTED;
  meshParentRssi = -128;
  meshTrickleReset();
}

static void meshAdoptParent(int idx, const MeshBeacon* b, int rssi, uint32_t now) {
  meshParentIdx         = idx;
  meshParentRank        = b->rank;
  meshParentIntervalMs  = b->beacon_interval_ms ? b->beacon_interval_ms
                                                : MESH_BEACON_INTERVAL_MIN_MS;
  meshParentLastHeardMs = now;
  meshParentRssi        = rssi;
  meshMyRank            = b->rank + 1;
  meshTxFailCount       = 0;
  meshTrickleReset();
  char m[13]; meshFormatMac(TRUSTED_NODES[idx].mac, m);
  Serial.printf("[mesh] parent=%s (rank %d, rssi %d) — my rank now %d\n",
                m, b->rank, rssi, meshMyRank);
}

// Process a trusted-or-not beacon. Caller has already length/magic-checked.
static void meshHandleBeacon(const uint8_t* srcMac, const MeshBeacon* b,
                             int rssi, uint32_t now) {
  int idx = meshTrustedIndex(srcMac);
  if (idx < 0) {
    // Untrusted device: never a routing candidate, whatever it advertises.
    char m[13]; meshFormatMac(srcMac, m);
    Serial.printf("[mesh] beacon from untrusted %s ignored\n", m);
    return;
  }

  if (meshNeighborLastHeard[idx] == 0) meshTrickleReset();  // new neighbor seen
  meshNeighborLastHeard[idx] = now;

  if (idx == meshParentIdx) {
    // Our current parent: refresh liveness + track its interval/rank drift.
    meshParentLastHeardMs = now;
    meshParentRssi        = rssi;
    meshParentIntervalMs  = b->beacon_interval_ms ? b->beacon_interval_ms
                                                  : MESH_BEACON_INTERVAL_MIN_MS;
    meshTxFailCount       = 0;
    // Window duration propagates downward from the parent (bridge-originated).
    meshWindowDurationMs  = b->window_duration_ms;
    if (b->rank == MESH_RANK_UNROUTED) { meshDropParent("parent became unrouted"); return; }
    if ((uint8_t)(b->rank + 1) != meshMyRank) {  // parent's rank moved — follow it
      meshParentRank = b->rank;
      meshMyRank     = b->rank + 1;
      meshTrickleReset();
      Serial.printf("[mesh] parent rank changed — my rank now %d\n", meshMyRank);
    }
    return;
  }

  // Strict rank rule (loop-safe by construction): only a strictly lower rank
  // may be a parent candidate. Among candidates: lowest rank, RSSI tiebreak.
  if (b->rank >= meshMyRank) return;
  if (meshParentIdx >= 0 &&
      !(b->rank < meshParentRank ||
        (b->rank == meshParentRank && rssi > meshParentRssi))) return;
  meshAdoptParent(idx, b, rssi, now);
}

static void meshCheckParentTimeout(uint32_t now) {
  if (meshParentIdx < 0) return;
  if (now - meshParentLastHeardMs >
      (uint32_t)MESH_PARENT_TIMEOUT_FACTOR * meshParentIntervalMs)
    meshDropParent("beacon timeout");
}

static bool meshHasParent() { return meshParentIdx >= 0; }

// Backstop for a parent that dies mid-trickle (its advertised interval may be
// 60s, i.e. 180s beacon timeout): 3 consecutive unicast delivery failures drop
// it immediately. Broadcasts always report success, so only unicast failures
// accumulate; the counter resets on any beacon heard from the parent.
static void meshNotifyTxStatus(bool ok) {
  if (ok) return;
  if (++meshTxFailCount >= 3) {
    meshTxFailCount = 0;
    if (meshParentIdx >= 0) meshDropParent("3 consecutive tx failures");
  }
}

// ── De-dup cache ──────────────────────────────────────────────────────────────
// Returns true if (origin, seq) was already seen; otherwise records it.
static bool meshDedupSeen(const uint8_t* originMac, uint16_t seq) {
  for (int i = 0; i < MESH_DEDUP_CACHE_SIZE; i++)
    if (meshDedup[i].used && meshDedup[i].seq == seq &&
        meshMacEqual(meshDedup[i].mac, originMac)) return true;
  memcpy(meshDedup[meshDedupNext].mac, originMac, 6);
  meshDedup[meshDedupNext].seq  = seq;
  meshDedup[meshDedupNext].used = true;
  meshDedupNext = (meshDedupNext + 1) % MESH_DEDUP_CACHE_SIZE;
  return false;
}

// ── Data path ─────────────────────────────────────────────────────────────────
static bool meshUnicastToParent(const MeshDataPacket* pkt) {
  if (meshParentIdx < 0) return false;
  return esp_now_send(TRUSTED_NODES[meshParentIdx].mac,
                      (const uint8_t*)pkt, sizeof(*pkt)) == ESP_OK;
}

static void meshBufferPush(const MeshDataPacket* pkt) {
  int tail = (meshBufHead + meshBufCount) % MESH_DATA_BUFFER_SIZE;
  meshBuf[tail] = *pkt;
  if (meshBufCount < MESH_DATA_BUFFER_SIZE) meshBufCount++;
  else meshBufHead = (meshBufHead + 1) % MESH_DATA_BUFFER_SIZE;  // oldest dropped
}

static void meshFlushBuffer() {
  while (meshBufCount > 0 && meshParentIdx >= 0) {
    if (!meshUnicastToParent(&meshBuf[meshBufHead])) break;
    meshBufHead = (meshBufHead + 1) % MESH_DATA_BUFFER_SIZE;
    meshBufCount--;
  }
  if (meshBufCount == 0) meshBufHead = 0;
}

// Send own reading now, or buffer it while isolated (spec Routing step 5).
// Buffered readings retry automatically on the first routed send cycle.
static void meshSendReading(const SensorPacket* payload) {
  MeshDataPacket pkt;
  pkt.magic = MESH_MAGIC;
  memcpy(pkt.origin_mac, meshSelfMac, 6);
  pkt.origin_rank = meshMyRank;
  pkt.ttl         = MESH_MAX_TTL;
  pkt.seq         = meshDataSeq++;
  pkt.payload     = *payload;
  if (meshParentIdx < 0) {
    meshBufferPush(&pkt);
    Serial.printf("[mesh] unrouted — reading buffered (%d queued)\n", meshBufCount);
    return;
  }
  if (meshBufCount > 0) {
    Serial.printf("[mesh] routed again — flushing %d buffered readings\n", meshBufCount);
    meshFlushBuffer();
  }
  meshUnicastToParent(&pkt);
}

// A child unicast us a data packet: forward it toward the bridge
// (spec Routing step 4). Untrusted senders can't reach here in practice — no
// encrypted-peer relationship means their frames fail to decrypt — but the
// explicit trust check documents intent and costs nothing.
static void meshRelayData(const uint8_t* srcMac, const uint8_t* data, int len) {
  if (len != (int)sizeof(MeshDataPacket)) return;
  if (meshTrustedIndex(srcMac) < 0) return;
  MeshDataPacket pkt;
  memcpy(&pkt, data, sizeof(pkt));
  if (pkt.magic != MESH_MAGIC) return;
  if (pkt.ttl == 0) { Serial.println("[mesh] ttl expired — dropped"); return; }
  if (meshDedupSeen(pkt.origin_mac, pkt.seq)) return;  // route-flap duplicate
  if (meshParentIdx < 0) { Serial.println("[mesh] relay with no parent — dropped"); return; }
  pkt.ttl--;
  meshUnicastToParent(&pkt);
  char m[13]; meshFormatMac(pkt.origin_mac, m);
  Serial.printf("[mesh] relayed packet from %s (ttl now %d)\n", m, pkt.ttl);
}
```

- [ ] **Step 2: Manual verification — the core compiles standalone**

Scratch sketch (File → New Sketch, do not commit):

```cpp
#include <esp_now.h>
#include <WiFi.h>
#include <mesh_config.h>
#include <mesh_node.h>

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  esp_now_init();
  meshInit(0);
  SensorPacket p = { 21.0f, 50.0f, 40.0f };
  meshSendReading(&p);           // unrouted → must log "reading buffered (1 queued)"
  meshBeaconTick(millis());
  meshCheckParentTimeout(millis());
  Serial.printf("sizes: beacon=%u data=%u parent=%d\n",
                (unsigned)sizeof(MeshBeacon), (unsigned)sizeof(MeshDataPacket),
                (int)meshHasParent());
}
void loop() {}
```

Board **ESP32C3 Dev Module** → Verify → expected `Done compiling`. Then switch board to **ESP32 Dev Module** and Verify again (the WROOM/Xtensa target must also compile clean). Optionally flash it to a spare board and confirm the serial output includes `sizes: beacon=18 data=23 parent=0` and the buffered-reading log line. Discard the scratch sketch.

- [ ] **Step 3: Commit**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_node.h
git commit -m "feat: add GreenhouseMesh routing core (rank/trickle/dedup/relay/buffering)"
```

---

### Task 3: Edge node integration (both variants)

**Files:**
- Rewrite: `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` (all 135 lines)
- Rewrite: `firmware/edge_node_esp32/edge_node_esp32.ino` (all 134 lines)

**Interfaces:**
- Consumes: `mesh_config.h` constants (Task 1); from `mesh_node.h` (Task 2): `SensorPacket`, `MeshBeacon`, `MeshDataPacket`, `meshInit`, `meshBeaconTick`, `meshCheckParentTimeout`, `meshHandleBeacon`, `meshRelayData`, `meshSendReading`, `meshNotifyTxStatus`, `meshHasParent`.
- Produces: nothing new for later tasks — these sketches are leaves. Behavior contract for Task 5: a node beacons on the trickle schedule, adopts the lowest-rank trusted neighbor as parent, relays children's packets, and buffers its own readings while unrouted.

What changes vs. today, grounded in current lines (C3 variant; WROOM offsets are within ±2 lines):

| Current code | Fate |
|---|---|
| `bridgeMac[]` (line 20) | **Deleted** — parent is chosen dynamically |
| local `SensorPacket` typedef (lines 29-33) | **Deleted** — comes from `mesh_node.h` |
| `esp_now_peer_info_t peer` + `failCount` (lines 36-37) | **Deleted** — peers registered by `meshInit`; fail handling moves to `meshNotifyTxStatus` |
| `getWiFiChannel` (lines 39-45), `soilPercent` (lines 47-52) | Kept as-is |
| `onDataSent` (lines 54-62) | Reduced to a `meshNotifyTxStatus` forward |
| setup peer registration (lines 91-94) | Replaced by `meshInit(0)` + recv/send callback registration (edges gain a recv callback — they never had one) |
| blocking `loop()` (lines 100-134): `delay(2000)` warm-up + `delay(SEND_INTERVAL_MS)` | Replaced by a millis()-driven two-phase state machine — blocking 7s per cycle would starve the 2s beacon cadence and falsely trip the 6s minimum parent timeout |
| `failCount >= 3` channel re-scan (lines 122-131) | Replaced by: 3 tx failures → parent drop (in `mesh_node.h`), plus a channel re-scan only after `MESH_RESCAN_AFTER_MS` continuously unrouted (router-channel-change recovery, same self-healing intent) |
| double `analogRead` after sensor power-off (line 116 — a latent bug: reads soil with the sensor unpowered) | Fixed: read once, while powered |

**Channel discovery note (deliberate non-change):** nodes still find the ESP-NOW channel by scanning for the router SSID at boot (lines 77-83), exactly as today. A beacon-based multi-channel search for nodes beyond *router* range is out of scope — in this deployment the router's range comfortably exceeds ESP-NOW node-to-node range, and the spec doesn't ask for it.

- [ ] **Step 1: Rewrite `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino`**

Replace the full file contents:

```cpp
#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <DHT.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── Pin definitions ───────────────────────────────────────────────────────────
#define SOIL_DATA_PIN  2   // ADC1_CH2
#define DHT_DATA_PIN   6   // GPIO6 — moved away from JTAG pins
#define SOIL_PWR_PIN   4
#define DHT_PWR_PIN    5

// ── Soil moisture calibration ─────────────────────────────────────────────────
// Read SOIL_DATA_PIN with sensor in dry air → set DRY_VAL
// Read SOIL_DATA_PIN with sensor submerged in water → set WET_VAL
#define SOIL_DRY_VAL  3163
#define SOIL_WET_VAL  1529

// ── Network (channel scan only — never connects) ──────────────────────────────
#define WIFI_SSID "TP-Link_14A6"

// ── Timing ────────────────────────────────────────────────────────────────────
#define SEND_INTERVAL_MS  5000   // must match MESH_EXPECTED_REPORT_INTERVAL_MS
#define SENSOR_WARMUP_MS  2000   // sensor power-up settle time

DHT dht(DHT_DATA_PIN, DHT22);

// Non-blocking sensor cycle: blocking delay()s would starve the beacon/timeout
// scheduling in loop(), so the old delay(2000)+delay(5000) cycle is now a
// two-phase state machine driven by millis().
enum SensorPhase { PHASE_IDLE, PHASE_WARMUP };
SensorPhase phase        = PHASE_IDLE;
uint32_t    phaseStartMs = 0;
uint32_t    lastCycleMs  = 0;
uint32_t    lastRescanMs = 0;

int32_t getWiFiChannel(const char* ssid) {
  int32_t n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    if (strcmp(ssid, WiFi.SSID(i).c_str()) == 0) return WiFi.channel(i);
  }
  return 1;
}

float soilPercent(int raw) {
  float pct = 100.0f * (SOIL_DRY_VAL - raw) / (float)(SOIL_DRY_VAL - SOIL_WET_VAL);
  if (pct < 0)   pct = 0;
  if (pct > 100) pct = 100;
  return pct;
}

void onDataSent(const wifi_tx_info_t* info, esp_now_send_status_t status) {
  meshNotifyTxStatus(status == ESP_NOW_SEND_SUCCESS);
}

void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  uint32_t now = millis();
  int rssi = info->rx_ctrl ? info->rx_ctrl->rssi : -127;
  if (len == sizeof(MeshBeacon)) {
    MeshBeacon b;
    memcpy(&b, data, sizeof(b));
    if (b.magic == MESH_MAGIC) meshHandleBeacon(info->src_addr, &b, rssi, now);
  } else if (len == sizeof(MeshDataPacket)) {
    // Some child picked us as its parent — relay its packet toward the bridge.
    meshRelayData(info->src_addr, data, len);
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // wait for USB CDC to connect on C3

  pinMode(SOIL_PWR_PIN, OUTPUT);
  pinMode(DHT_PWR_PIN,  OUTPUT);
  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);

  dht.begin();

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  Serial.print("[wifi] scanning channel for " WIFI_SSID "...");
  int32_t ch = getWiFiChannel(WIFI_SSID);
  Serial.printf(" ch%d\n", ch);

  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    Serial.println("[esp-now] init failed, rebooting");
    ESP.restart();
  }
  esp_now_register_send_cb(onDataSent);
  esp_now_register_recv_cb(onDataRecv);
  meshInit(0);  // channel 0 = follow current radio channel (survives re-scans)

  Serial.printf("[edge] MAC: %s\n", WiFi.macAddress().c_str());
  Serial.println("[edge] unrouted — listening for trusted beacons");
}

void loop() {
  uint32_t now = millis();

  meshBeaconTick(now);          // own beacon on the trickle schedule
  meshCheckParentTimeout(now);  // self-heal: drop a silent parent

  switch (phase) {
    case PHASE_IDLE:
      if (now - lastCycleMs >= SEND_INTERVAL_MS) {
        digitalWrite(SOIL_PWR_PIN, HIGH);
        digitalWrite(DHT_PWR_PIN,  HIGH);
        phaseStartMs = now;
        phase = PHASE_WARMUP;
      }
      break;

    case PHASE_WARMUP:
      if (now - phaseStartMs >= SENSOR_WARMUP_MS) {
        SensorPacket pkt;
        pkt.temperature   = dht.readTemperature();
        pkt.humidity      = dht.readHumidity();
        pkt.soil_moisture = soilPercent(analogRead(SOIL_DATA_PIN));  // read while powered

        digitalWrite(SOIL_PWR_PIN, LOW);
        digitalWrite(DHT_PWR_PIN,  LOW);
        lastCycleMs = now;
        phase = PHASE_IDLE;

        if (isnan(pkt.temperature) || isnan(pkt.humidity)) {
          Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO6");
        } else {
          Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%%\n",
                        pkt.temperature, pkt.humidity, pkt.soil_moisture);
          meshSendReading(&pkt);  // to parent, or buffered while unrouted
        }
      }
      break;
  }

  // Continuously unrouted for a minute → maybe the router changed channels.
  // Re-scan and retune (peers use channel 0, so no re-registration needed).
  if (!meshHasParent()) {
    if (now - lastRescanMs >= MESH_RESCAN_AFTER_MS) {
      lastRescanMs = now;
      Serial.println("[esp-now] unrouted too long — re-scanning WiFi channel");
      int32_t ch = getWiFiChannel(WIFI_SSID);
      esp_wifi_set_promiscuous(true);
      esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
      esp_wifi_set_promiscuous(false);
      Serial.printf("[esp-now] tuned to ch%d\n", ch);
    }
  } else {
    lastRescanMs = now;
  }

  delay(10);  // yield; keeps the loop responsive without busy-spinning
}
```

- [ ] **Step 2: Verify the C3 sketch compiles**

Arduino IDE → open `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` → board **ESP32C3 Dev Module** (USB CDC On Boot: Enabled) → Verify.
Expected: `Done compiling`, no warnings about redefined `SensorPacket` (the local typedef must be gone).

- [ ] **Step 3: Rewrite `firmware/edge_node_esp32/edge_node_esp32.ino`**

Replace the full file contents (identical mesh logic; only pins, the setup delay, and the DHT-pin message differ — repeated in full so this task reads standalone):

```cpp
#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <DHT.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── Pin definitions (ESP32 WROOM-32) ─────────────────────────────────────────
#define DHT_DATA_PIN   4   // GPIO4
#define SOIL_DATA_PIN  34  // GPIO34 — ADC1, input-only
#define DHT_PWR_PIN    26  // GPIO26
#define SOIL_PWR_PIN   27  // GPIO27

// ── Soil moisture calibration ─────────────────────────────────────────────────
// Read SOIL_DATA_PIN with sensor in dry air → set DRY_VAL
// Read SOIL_DATA_PIN with sensor submerged in water → set WET_VAL
#define SOIL_DRY_VAL  3163
#define SOIL_WET_VAL  1529

// ── Network (channel scan only — never connects) ──────────────────────────────
#define WIFI_SSID "TP-Link_14A6"

// ── Timing ────────────────────────────────────────────────────────────────────
#define SEND_INTERVAL_MS  5000   // must match MESH_EXPECTED_REPORT_INTERVAL_MS
#define SENSOR_WARMUP_MS  2000   // sensor power-up settle time

DHT dht(DHT_DATA_PIN, DHT22);

// Non-blocking sensor cycle — see the C3 variant for rationale.
enum SensorPhase { PHASE_IDLE, PHASE_WARMUP };
SensorPhase phase        = PHASE_IDLE;
uint32_t    phaseStartMs = 0;
uint32_t    lastCycleMs  = 0;
uint32_t    lastRescanMs = 0;

int32_t getWiFiChannel(const char* ssid) {
  int32_t n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    if (strcmp(ssid, WiFi.SSID(i).c_str()) == 0) return WiFi.channel(i);
  }
  return 1;
}

float soilPercent(int raw) {
  float pct = 100.0f * (SOIL_DRY_VAL - raw) / (float)(SOIL_DRY_VAL - SOIL_WET_VAL);
  if (pct < 0)   pct = 0;
  if (pct > 100) pct = 100;
  return pct;
}

void onDataSent(const wifi_tx_info_t* info, esp_now_send_status_t status) {
  meshNotifyTxStatus(status == ESP_NOW_SEND_SUCCESS);
}

void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  uint32_t now = millis();
  int rssi = info->rx_ctrl ? info->rx_ctrl->rssi : -127;
  if (len == sizeof(MeshBeacon)) {
    MeshBeacon b;
    memcpy(&b, data, sizeof(b));
    if (b.magic == MESH_MAGIC) meshHandleBeacon(info->src_addr, &b, rssi, now);
  } else if (len == sizeof(MeshDataPacket)) {
    // Some child picked us as its parent — relay its packet toward the bridge.
    meshRelayData(info->src_addr, data, len);
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(DHT_PWR_PIN,  OUTPUT);
  pinMode(SOIL_PWR_PIN, OUTPUT);
  digitalWrite(DHT_PWR_PIN,  LOW);
  digitalWrite(SOIL_PWR_PIN, LOW);

  dht.begin();

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  Serial.print("[wifi] scanning channel for " WIFI_SSID "...");
  int32_t ch = getWiFiChannel(WIFI_SSID);
  Serial.printf(" ch%d\n", ch);

  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    Serial.println("[esp-now] init failed, rebooting");
    ESP.restart();
  }
  esp_now_register_send_cb(onDataSent);
  esp_now_register_recv_cb(onDataRecv);
  meshInit(0);  // channel 0 = follow current radio channel (survives re-scans)

  Serial.printf("[edge] MAC: %s\n", WiFi.macAddress().c_str());
  Serial.println("[edge] unrouted — listening for trusted beacons");
}

void loop() {
  uint32_t now = millis();

  meshBeaconTick(now);          // own beacon on the trickle schedule
  meshCheckParentTimeout(now);  // self-heal: drop a silent parent

  switch (phase) {
    case PHASE_IDLE:
      if (now - lastCycleMs >= SEND_INTERVAL_MS) {
        digitalWrite(DHT_PWR_PIN,  HIGH);
        digitalWrite(SOIL_PWR_PIN, HIGH);
        phaseStartMs = now;
        phase = PHASE_WARMUP;
      }
      break;

    case PHASE_WARMUP:
      if (now - phaseStartMs >= SENSOR_WARMUP_MS) {
        SensorPacket pkt;
        pkt.temperature   = dht.readTemperature();
        pkt.humidity      = dht.readHumidity();
        pkt.soil_moisture = soilPercent(analogRead(SOIL_DATA_PIN));  // read while powered

        digitalWrite(DHT_PWR_PIN,  LOW);
        digitalWrite(SOIL_PWR_PIN, LOW);
        lastCycleMs = now;
        phase = PHASE_IDLE;

        if (isnan(pkt.temperature) || isnan(pkt.humidity)) {
          Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO4");
        } else {
          Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%%\n",
                        pkt.temperature, pkt.humidity, pkt.soil_moisture);
          meshSendReading(&pkt);  // to parent, or buffered while unrouted
        }
      }
      break;
  }

  // Continuously unrouted for a minute → maybe the router changed channels.
  // Re-scan and retune (peers use channel 0, so no re-registration needed).
  if (!meshHasParent()) {
    if (now - lastRescanMs >= MESH_RESCAN_AFTER_MS) {
      lastRescanMs = now;
      Serial.println("[esp-now] unrouted too long — re-scanning WiFi channel");
      int32_t ch = getWiFiChannel(WIFI_SSID);
      esp_wifi_set_promiscuous(true);
      esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
      esp_wifi_set_promiscuous(false);
      Serial.printf("[esp-now] tuned to ch%d\n", ch);
    }
  } else {
    lastRescanMs = now;
  }

  delay(10);  // yield; keeps the loop responsive without busy-spinning
}
```

- [ ] **Step 4: Verify the WROOM sketch compiles**

Arduino IDE → open `firmware/edge_node_esp32/edge_node_esp32.ino` → board **ESP32 Dev Module** → Verify.
Expected: `Done compiling`.

- [ ] **Step 5: Commit**

```bash
git add firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino firmware/edge_node_esp32/edge_node_esp32.ino
git commit -m "feat: mesh routing on edge nodes (parent selection, relay, trickle beacons, buffering)"
```

---

### Task 4: Bridge integration

**Files:**
- Rewrite: `firmware/bridge_esp32/bridge_esp32.ino` (all 141 lines)

**Interfaces:**
- Consumes: `mesh_config.h` (Task 1); from `mesh_node.h` (Task 2): `MeshBeacon`, `MeshDataPacket`, `meshInit`, `meshSendBeaconNow`, `meshTrustedIndex`, `meshFormatMac`, `meshDedupSeen`.
- Produces (MQTT contract Task 5 verifies): `greenhouse/<zone>/air/temperature|air/humidity|soil/moisture` published with **`retain=true`** (was false — fixes the `HANDOFF.md` backlog item about empty zone cards after broker restart); `greenhouse/nodes/<origin-mac>/status` = `"online"` on every packet and `"offline"` after `MESH_OFFLINE_AFTER × MESH_EXPECTED_REPORT_INTERVAL_MS` of silence (both retained, so the app sees the last-known status on reconnect — the offline transition is published once and would otherwise be invisible to late subscribers).

What changes vs. today, grounded in current lines:

| Current code | Fate |
|---|---|
| `ZONES[]` + `ZoneEntry` (lines 18-24) | **Deleted** — superseded by `TRUSTED_NODES[]` |
| local `SensorPacket` typedef (lines 26-31) | **Deleted** — comes from `mesh_node.h` |
| `zoneForMac()` string lookup (lines 37-42) | Replaced by binary-MAC `meshTrustedIndex` on **`origin_mac`** — the immediate ESP-NOW sender may now be a relay, not the origin (the one functional lookup change in the spec) |
| `mqttPublish` (lines 44-48) | Gains a `retain` parameter |
| `onDataRecv` (lines 65-107) | Rewritten for `MeshDataPacket` (magic check, dedup, origin lookup, last-seen tracking). Also fixes the latent `%d`-for-float format bug on line 87. |
| `setup` (lines 109-136) | Adds `meshInit(0)` after `esp_now_init` (PMK + encrypted peers for every trusted node) |
| `loop` (lines 138-141) | Adds the fixed-interval rank-0 beacon and the offline-status check |

- [ ] **Step 1: Rewrite `firmware/bridge_esp32/bridge_esp32.ino`**

Replace the full file contents:

```cpp
#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── WiFi (home router) ────────────────────────────────────────────────────────
#define WIFI_SSID     "TP-Link_14A6"
#define WIFI_PASSWORD "6940604664"   // ← fill in

// ── Pi MQTT broker ────────────────────────────────────────────────────────────
#define MQTT_HOST     "greenhouse.local"
#define MQTT_PORT     8883
#define MQTT_USER     "app"
#define MQTT_PASS     "tCCprsQSqwT072X6WRTr"

// (Zone mapping now lives in mesh_config.h's TRUSTED_NODES[] — shared with the
// edge nodes. SensorPacket comes from mesh_node.h.)

// ── MQTT client ───────────────────────────────────────────────────────────────
WiFiClientSecure net;
PubSubClient     mqtt(net);

// ── Node liveness (spec: Bridge Changes — offline detection) ──────────────────
uint32_t lastSeenMs[TRUSTED_NODE_COUNT];
bool     nodeOnline[TRUSTED_NODE_COUNT];
uint32_t lastBeaconMs       = 0;
uint32_t lastOfflineCheckMs = 0;

void mqttPublish(const char* topic, const char* payload, bool retain) {
  if (!mqtt.connected()) return;
  mqtt.publish(topic, payload, retain);
  Serial.printf("  → %s  %s%s\n", topic, payload, retain ? "  (retained)" : "");
}

void reconnectMQTT() {
  while (!mqtt.connected()) {
    Serial.print("[mqtt] connecting... ");
    String id = "gh-bridge-";
    id += String((uint32_t)ESP.getEfuseMac(), HEX);
    if (mqtt.connect(id.c_str(), MQTT_USER, MQTT_PASS)) {
      Serial.println("OK");
    } else {
      Serial.printf("failed rc=%d, retry in 5s\n", mqtt.state());
      delay(5000);
      // Note: while MQTT is down this loop also pauses rank-0 beacons, so
      // children go unrouted and buffer their readings — which is exactly
      // the right behavior during a broker outage.
    }
  }
}

// ── ESP-NOW receive callback ──────────────────────────────────────────────────
void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  if (len == sizeof(MeshBeacon)) return;  // neighbor beacons: bridge doesn't route
  if (len != sizeof(MeshDataPacket)) {
    Serial.printf("[esp-now] bad packet size %d\n", len);
    return;
  }

  MeshDataPacket pkt;
  memcpy(&pkt, data, sizeof(pkt));
  if (pkt.magic != MESH_MAGIC) { Serial.println("[esp-now] bad magic, dropped"); return; }
  if (meshDedupSeen(pkt.origin_mac, pkt.seq)) return;  // route-flap duplicate

  // Zone lookup by ORIGIN, not the immediate sender — with relaying, the
  // ESP-NOW src_addr may be an intermediate hop, not the node that measured.
  char mac[13];
  meshFormatMac(pkt.origin_mac, mac);
  int idx = meshTrustedIndex(pkt.origin_mac);
  if (idx < 0 || TRUSTED_NODES[idx].zone == nullptr) {
    Serial.printf("[esp-now] unknown origin %s — add to TRUSTED_NODES[]\n", mac);
    return;
  }
  const char* zone = TRUSTED_NODES[idx].zone;
  lastSeenMs[idx] = millis();
  nodeOnline[idx] = true;

  Serial.printf("[esp-now] %s (zone=%s rank=%d ttl=%d) T=%.1f H=%.1f Soil=%.1f\n",
                mac, zone, pkt.origin_rank, pkt.ttl,
                pkt.payload.temperature, pkt.payload.humidity,
                pkt.payload.soil_moisture);

  if (!mqtt.connected()) { Serial.println("  MQTT not ready, packet dropped"); return; }

  char topic[64], payload[16];

  // retain=true: zone cards must survive a broker restart (HANDOFF.md backlog).
  snprintf(topic, sizeof(topic), "greenhouse/%s/air/temperature", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.payload.temperature);
  mqttPublish(topic, payload, true);

  snprintf(topic, sizeof(topic), "greenhouse/%s/air/humidity", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.payload.humidity);
  mqttPublish(topic, payload, true);

  snprintf(topic, sizeof(topic), "greenhouse/%s/soil/moisture", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.payload.soil_moisture);
  mqttPublish(topic, payload, true);

  snprintf(topic, sizeof(topic), "greenhouse/nodes/%s/status", mac);
  mqttPublish(topic, "online", true);
}

// Publish "offline" once a node misses MESH_OFFLINE_AFTER expected reports —
// distinguishes "node is dead" from "data arriving via a longer path" (which
// still refreshes lastSeenMs through the relay chain).
void checkOfflineNodes(uint32_t now) {
  if (now - lastOfflineCheckMs < 1000) return;
  lastOfflineCheckMs = now;
  for (int i = 0; i < TRUSTED_NODE_COUNT; i++) {
    if (TRUSTED_NODES[i].zone == nullptr) continue;  // skip the bridge entry
    if (!nodeOnline[i]) continue;                    // already reported offline
    if (now - lastSeenMs[i] >
        (uint32_t)MESH_OFFLINE_AFTER * MESH_EXPECTED_REPORT_INTERVAL_MS) {
      nodeOnline[i] = false;
      char mac[13], topic[64];
      meshFormatMac(TRUSTED_NODES[i].mac, mac);
      snprintf(topic, sizeof(topic), "greenhouse/nodes/%s/status", mac);
      mqttPublish(topic, "offline", true);
      Serial.printf("[bridge] node %s (%s) → offline\n", mac, TRUSTED_NODES[i].zone);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // wait for USB CDC to connect on C3

  // Print own MAC so it can be checked against TRUSTED_NODES[]
  WiFi.mode(WIFI_STA);
  Serial.printf("\n[bridge] MAC: %s\n", WiFi.macAddress().c_str());

  // Connect WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[wifi] connecting");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.printf("\n[wifi] connected, IP=%s\n", WiFi.localIP().toString().c_str());

  // MQTT — skip cert verification (self-signed, local network)
  net.setInsecure();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  reconnectMQTT();

  // ESP-NOW + mesh
  if (esp_now_init() != ESP_OK) {
    Serial.println("[esp-now] init failed");
    return;
  }
  esp_now_register_recv_cb(onDataRecv);
  meshInit(0);  // PMK + encrypted peers for every trusted node; channel 0 =
                // follow the STA's current channel (the router's)

  // Baseline liveness at boot: a node that never reports goes offline after
  // the missed-report threshold instead of staying "unknown" forever.
  uint32_t now = millis();
  for (int i = 0; i < TRUSTED_NODE_COUNT; i++) {
    lastSeenMs[i] = now;
    nodeOnline[i] = true;
  }

  Serial.println("[bridge] ready — beaconing at rank 0");
}

void loop() {
  if (!mqtt.connected()) reconnectMQTT();
  mqtt.loop();

  uint32_t now = millis();

  // Rank-0 anchor beacon: fixed short interval, no trickle — mains-powered,
  // so there is no cost pressure (spec: Architecture).
  if (now - lastBeaconMs >= MESH_BRIDGE_BEACON_INTERVAL_MS) {
    lastBeaconMs = now;
    meshSendBeaconNow(0, MESH_BRIDGE_BEACON_INTERVAL_MS);
  }

  checkOfflineNodes(now);
}
```

- [ ] **Step 2: Verify the bridge sketch compiles**

Arduino IDE → open `firmware/bridge_esp32/bridge_esp32.ino` → board **ESP32C3 Dev Module** (the bridge is a C3 Super Mini, per `docs/ESP_NOW_BRIDGE_PROGRESS.md`) → Verify.
Expected: `Done compiling`.

- [ ] **Step 3: Commit**

```bash
git add firmware/bridge_esp32/bridge_esp32.ino
git commit -m "feat: bridge mesh support (rank-0 beacon, origin zone lookup, offline status, retain=true)"
```

---

### Task 5: Bench-test validation (spec Testing section)

**Files:** none (hardware validation; fix-forward + commit any firmware fixes it surfaces)

**Interfaces:**
- Consumes: everything from Tasks 1–4, flashed to real hardware.
- Produces: a validated mesh; any tuning-constant adjustments the bench demands go into `mesh_config.h` (the spec explicitly allows this).

Hardware/setup: bridge C3 plugged into the Pi for power, both edge nodes powered, Pi running Mosquitto. Monitor MQTT from an SSH session on the Pi:

```bash
mosquitto_sub -h localhost -p 1883 -u app -P tCCprsQSqwT072X6WRTr -t 'greenhouse/#' -v
```

Keep a serial monitor (115200 baud) on whichever device each scenario watches. **Flash order: bridge first, then both edges** — the old and new wire formats are incompatible, so mixed-fleet frames are dropped as `bad packet size` until all three are reflashed (harmless, but expect noise mid-flash).

- [ ] **Step 1: Flash all three devices**

Upload Task 4's bridge sketch and Task 3's two edge sketches to their boards. Expected on each edge's serial: `[edge] unrouted — listening for trusted beacons`, then within ~2 s `[mesh] parent=206EF16C6B50 (rank 0, rssi …) — my rank now 1`. Expected on the bridge: `[bridge] ready — beaconing at rank 0`.

- [ ] **Step 2: Scenario 1 — baseline regression (rank-1 star behavior + retain fix)**

Both edges in direct range of the bridge. Expected: the `mosquitto_sub` stream shows `greenhouse/zone1/...` and `greenhouse/zone2/...` readings every ~5 s and `greenhouse/nodes/<mac>/status online`, exactly like the pre-mesh firmware. Bridge serial shows `rank=1 ttl=4` on every packet (no relaying happened). Then verify the retain fix: kill and restart the subscriber — it must immediately receive the last reading for every topic (retained), without waiting for the next packet. Also confirm trickle settling: each edge's beacon log gaps grow 2s → 4s → … → 60s while the topology is stable.

- [ ] **Step 3: Scenario 2 — forced 2-hop relay**

Take the zone2 node beyond direct bridge range: physically separate it, or temporarily lower its TX power by adding `esp_wifi_set_max_tx_power(8);` right after `meshInit(0);` in its setup and reflashing (remove the line afterward). Expected: zone2's serial shows it adopting **zone1** as parent (`rank 1`) and `my rank now 2`; zone1's serial shows `[mesh] relayed packet from 88F155314564 (ttl now 3)`; the bridge publishes `greenhouse/zone2/...` with the correct zone (origin-MAC lookup) and logs `rank=2 ttl=3`.

- [ ] **Step 4: Scenario 3 — kill the intermediate node (self-healing)**

With the Step 3 topology running, power off zone1 (the relay). Expected on zone2's serial, within 3× zone1's last-advertised beacon interval (6 s if recently reset, up to 180 s if fully settled — or sooner via `3 consecutive tx failures`): `[mesh] parent lost … — unrouted, rediscovering`, followed by either adopting the bridge directly (if marginally in range) or `[mesh] unrouted — reading buffered (N queued)`. If it stays isolated: power zone1 back on and confirm zone2 re-adopts it and logs `routed again — flushing N buffered readings`, and the buffered readings arrive at the bridge.

- [ ] **Step 5: Scenario 4 — offline status transition**

While zone1 is powered off (Step 4), watch `greenhouse/nodes/206EF16CA1B0/status` on the subscriber. Expected: `offline` published within ~15 s of zone1's last packet (3 × 5 s), and the bridge serial logs `[bridge] node 206EF16CA1B0 (zone1) → offline`. Power zone1 back on: status returns to `online` with its first packet. Confirm the status is retained: restart the subscriber while a node is offline and verify `offline` arrives immediately.

- [ ] **Step 6: Scenario 5 — untrusted node is never a parent**

Simulate an unregistered device: comment out the `zone2` entry in `TRUSTED_NODES[]`, reflash **bridge and zone1** (not zone2 — it keeps beaconing with the full list, playing the "stranger" running the same beacon logic). Expected: zone1's serial logs `[mesh] beacon from untrusted 88F155314564 ignored` and never adopts it; the bridge drops zone2's data (`unknown origin` — and in practice the frames already fail to decrypt once the peer relationship is gone). Restore the entry and reflash all affected boards afterward. (With a spare 4th ESP32, flash it with edge firmware instead — same expected result, no fleet reflash needed.)

- [ ] **Step 7: Commit any fixes**

If the bench surfaced firmware fixes or tuning-constant changes:

```bash
git add firmware/
git commit -m "fix: mesh bench-test adjustments"
```

If everything passed untouched, there is nothing to commit — the feature is done.

---

## Requirements coverage

| Spec requirement | Where |
|---|---|
| Shared `mesh_config.h`: PMK/LMK, `TRUSTED_NODES[]`, tuning constants | Task 1 (path adjusted to `firmware/libraries/GreenhouseMesh/` for the Arduino toolchain — see File Structure note) |
| `MeshBeacon` / `MeshDataPacket` wire formats, `SensorPacket` wrapped unchanged | Task 2 step 1 |
| Strict-rank parent selection, RSSI tiebreak, loop-safe by construction | Task 2 (`meshHandleBeacon`) |
| Parent loss after 3× advertised beacon interval → unrouted → rediscovery | Task 2 (`meshCheckParentTimeout`, `meshDropParent`), validated Task 5 step 4 |
| Trickle beaconing: 2 s floor, doubling, 60 s cap, reset on any topology change | Task 2 (`meshBeaconTick`, `meshTrickleReset` calls on adopt/drop/rank-change/new-neighbor) |
| Network-wide encrypted peers at boot; beacons cleartext broadcast | Task 2 (`meshInit`) |
| Untrusted MACs ignored as parents and as data sources | Task 2 (`meshHandleBeacon`/`meshRelayData` trust checks), validated Task 5 step 6 |
| TTL backstop (4), de-dup cache (~32), relay forwarding | Task 2 (`meshRelayData`, `meshDedupSeen`) |
| Local buffering when isolated (10 most-recent, retry on re-route) | Task 2 (`meshBufferPush`/`meshFlushBuffer`/`meshSendReading`), validated Task 5 step 4 |
| `window_duration_ms` bridge-originated, propagated, unused today | Task 2 (beacon field + parent-branch copy), Task 4 (bridge sets it) |
| Edge nodes: replace direct-to-bridge send with mesh routing | Task 3 (both variants) |
| Bridge: rank-0 fixed beacon, encrypted peers, origin-MAC zone lookup | Task 4 |
| Bridge: offline-status publishing (3× missed reports) | Task 4 (`checkOfflineNodes`), validated Task 5 step 5 |
| Bridge: `retain=true` on sensor topics (HANDOFF backlog item) | Task 4, validated Task 5 step 2 |
| Manual bench plan, scenarios 1–5 | Task 5 (one step per spec scenario, plus flash + fix-forward) |
| Non-goals honored: no deep sleep, no per-pair keys, no dynamic provisioning, no automated tests | Global Constraints; nothing in any task implements them |

**Deliberate deviations from the spec (both justified inline):** (1) the shared header lives at `firmware/libraries/GreenhouseMesh/mesh_config.h` instead of `firmware/mesh_config.h` — Arduino's sketchbook-libraries mechanism is the only reliable way to share a header across sketches (File Structure note); (2) `greenhouse/nodes/<mac>/status` is published retained (the spec only mandates retain for sensor-reading topics) — a once-only `offline` transition would otherwise be invisible to any app that connects after it fires.

**Placeholders: none found.** All tuning constants carry the spec's concrete values; every code step contains the complete file or function contents; no TBD/TODO/"similar to Task N" references remain.
