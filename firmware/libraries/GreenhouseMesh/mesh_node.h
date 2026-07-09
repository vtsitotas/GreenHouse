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
