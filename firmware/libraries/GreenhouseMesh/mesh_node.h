#pragma once
// ── GreenhouseMesh: routing / trickle / relay core ────────────────────────────
// Header-only on purpose: every Arduino sketch is one translation unit, so the
// static state below is private per device and there is no library .cpp to link.
// Design: RPL-inspired strict rank ordering (a parent's advertised rank must be
// STRICTLY below our own) makes routing loops structurally impossible; TTL is a
// cheap backstop only. Trickle-style beacon backoff makes airtime cost
// proportional to instability, not wall-clock time.
//
// v2: peer-limit removed. Each node holds exactly two ESP-NOW peers:
//   broadcast — always registered; used for beacons and join beacons
//   current parent — registered dynamically; de-registered on parent change
// Trust/confidentiality moved to app layer: AES-GCM per sensor (AppKey, seals
// readings end-to-end) + AES-CMAC network tag (NetKey, lets relays cheaply
// reject garbage). The encrypted-peer model (7-peer cap, PMK/LMK) is gone.

#include <Arduino.h>
#include <esp_now.h>
#include <esp_sleep.h>
#include <WiFi.h>
#include <string.h>
#include "mesh_config.h"
#include "mesh_packet.h"
#include "mesh_store.h"
#include "mesh_crypto.h"

// ── Wire formats (spec: Packet Formats — do not reorder fields) ──────────────
// v2 data packets are the 61-byte sealed format from mesh_packet.h.
// Beacons remain broadcast/cleartext (broadcast frames cannot be ESP-NOW-
// encrypted), but now carry an 8-byte CMAC nettag over their own fields.

#define MESH_NETTAG_LEN_BEACON  MESH_NETTAG_LEN  // same 8-byte truncated CMAC

// Broadcast, authenticated (nettag over fields[0..sizeof-NETTAG-1]).
// Neighbor discovery + rank advertisement only — never sensor data.
typedef struct __attribute__((packed)) {
  uint8_t  magic;               // MESH_MAGIC_V2 (0x48)
  uint8_t  mac[6];              // sender's own MAC
  uint8_t  rank;                // sender's current rank (255 = unrouted)
  uint16_t seq;                 // monotonic per-sender counter
  uint32_t beacon_interval_ms;  // gap until sender's NEXT beacon
  uint32_t window_duration_ms;  // bridge-originated, propagated hop-by-hop
  uint8_t  flags;               // bit0 MESH_FLAG_SLEEPY
  uint8_t  tag[MESH_NETTAG_LEN]; // CMAC-AES(NetKey) over every preceding byte.
                                 // A node without the NetKey cannot forge this,
                                 // so it can never be adopted as a parent —
                                 // this is what replaces the old radio-layer
                                 // rejection of untrusted senders.
} MeshBeacon;                   // 27 bytes

// ── Join beacon (unenrolled nodes only) ──────────────────────────────────────
// Sent only by a node with no NetKey. Deliberately unauthenticated — the node
// has nothing to authenticate with yet — so relays do NOT forward it and it is
// only ever heard by a bridge in direct range. That proximity requirement is
// also the security property: you cannot enrol a sensor you are not standing
// next to. The app tells the owner to hold the sensor near the hub.
#define MESH_JOIN_MARKER             0x4A
#define MESH_JOIN_BEACON_INTERVAL_MS 3000UL

typedef struct __attribute__((packed)) {
  uint8_t magic;    // MESH_MAGIC_V2
  uint8_t marker;   // MESH_JOIN_MARKER — distinguishes this from a real beacon
  uint8_t mac[6];
} MeshJoinBeacon;   // 8 bytes

static const uint8_t MESH_BCAST[6] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };

// ── Keys (cached from NVS once per session) ───────────────────────────────────
static uint8_t meshAppKey[16];
static uint8_t meshNetKey[16];
static bool    meshKeysLoaded = false;

static bool meshLoadKeys() {
  if (meshKeysLoaded) return true;
  meshKeysLoaded = meshStoreAppKey(meshAppKey) && meshStoreNetKey(meshNetKey);
  return meshKeysLoaded;
}

// ── State ─────────────────────────────────────────────────────────────────────
static uint8_t  meshSelfMac[6];
static uint8_t  meshMyRank            = MESH_RANK_UNROUTED;
static uint8_t  meshParentMac[6]      = { 0 };   // MAC of current parent
static bool     meshHasParent_        = false;    // renamed to avoid clash with old fn
static uint8_t  meshParentRank        = MESH_RANK_UNROUTED;
static uint32_t meshParentIntervalMs  = MESH_BEACON_INTERVAL_MIN_MS;
static uint32_t meshParentLastHeardMs = 0;
static int      meshParentRssi        = -128;

static uint8_t  meshChannel = MESH_FIXED_CHANNEL;

static uint32_t meshBeaconIntervalMs  = MESH_BEACON_INTERVAL_MIN_MS;
static uint32_t meshLastBeaconMs      = 0;
static uint16_t meshBeaconSeq         = 0;
static uint32_t meshWindowDurationMs  = MESH_WINDOW_DURATION_MS;

// Fixed-size neighbor ring: no longer depends on compile-time fleet size.
#define MESH_NEIGHBOR_SLOTS 16
typedef struct { uint8_t mac[6]; uint32_t lastHeardMs; bool used; } MeshNeighbor;
static MeshNeighbor meshNeighbors[MESH_NEIGHBOR_SLOTS];

typedef struct { uint8_t mac[6]; uint16_t seq; uint32_t ms; bool used; } MeshDedupEntry;
static MeshDedupEntry meshDedup[MESH_DEDUP_CACHE_SIZE];
static int meshDedupNext = 0;

// Data buffer: now holds sealed 61-byte packets (MESH_PACKET_LEN) instead of
// the old plaintext MeshDataPacket struct. A buffered packet keeps its original
// seq and boot_count — exactly what makes the Pi's de-dup work on a retry.
static uint8_t  meshBuf[MESH_DATA_BUFFER_SIZE][MESH_PACKET_LEN];
static int      meshBufCount = 0;
static int      meshBufHead  = 0;   // oldest entry
static uint16_t meshDataSeq  = 0;
static int      meshTxFailCount = 0;

static uint16_t meshBatteryMv = 0;
static uint8_t  meshLastPkt[MESH_PACKET_LEN];
static bool     meshLastPktValid = false;

// ── Helpers ───────────────────────────────────────────────────────────────────
static bool meshMacEqual(const uint8_t* a, const uint8_t* b) {
  return memcmp(a, b, 6) == 0;
}

static void meshFormatMac(const uint8_t* mac, char* out) {  // out: 13 bytes
  snprintf(out, 13, "%02X%02X%02X%02X%02X%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

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

static bool meshIsSelfSleepy() { return meshStoreSleepy(); }

static void meshSetBatteryMv(uint16_t mv) { meshBatteryMv = mv; }

// ── Dynamic parent-peer registration ─────────────────────────────────────────
// The whole reason the 8-node ceiling is gone: a node registers its CURRENT
// parent and nothing else. Unencrypted peers mean we can still RECEIVE from any
// child without registering it (ESP-NOW registration gates sending, not
// receiving), so a relay never accumulates peers no matter how many children it
// serves.
static void meshClearParent() {
  if (meshHasParent_) esp_now_del_peer(meshParentMac);
  meshHasParent_ = false;
  memset(meshParentMac, 0, 6);
  meshMyRank = MESH_RANK_UNROUTED;
  meshParentRank = MESH_RANK_UNROUTED;
}

static bool meshSetParent(const uint8_t* mac, uint8_t rank, uint32_t intervalMs) {
  if (meshHasParent_ && meshMacEqual(meshParentMac, mac)) {
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
  meshHasParent_ = true;
  meshParentRank = rank;
  meshParentIntervalMs = intervalMs;
  meshMyRank = (uint8_t)(rank + 1);
  meshParentLastHeardMs = millis();
  return true;
}

// Public accessor (keeps the old call sites working)
static bool meshHasParent() { return meshHasParent_; }

// ── Init: broadcast peer only ─────────────────────────────────────────────────
// No per-node registration loop: that loop, with encrypt = true, is what
// capped the network at 8 devices. Peers are now broadcast + current parent.
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

// ── Beacon TX (trickle) ───────────────────────────────────────────────────────
static void meshTrickleReset() {
  meshBeaconIntervalMs = MESH_BEACON_INTERVAL_MIN_MS;
}

static void meshSendBeaconNow(uint8_t rank, uint32_t advertisedIntervalMs) {
  if (!meshLoadKeys()) return;  // no NetKey yet — cannot sign
  MeshBeacon b;
  b.magic               = MESH_MAGIC_V2;
  memcpy(b.mac, meshSelfMac, 6);
  b.rank               = rank;
  b.seq                = meshBeaconSeq++;
  b.beacon_interval_ms = advertisedIntervalMs;
  b.window_duration_ms = meshWindowDurationMs;
  b.flags              = meshIsSelfSleepy() ? MESH_FLAG_SLEEPY : 0;
  meshCmacTruncated(meshNetKey, (const uint8_t*)&b,
                    sizeof(MeshBeacon) - MESH_NETTAG_LEN, b.tag);
  esp_now_send(MESH_BCAST, (const uint8_t*)&b, sizeof(b));
}

// Edge-node beacon scheduler: send when due, then double the interval (capped).
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
  meshClearParent();
  meshParentRssi = -128;
  meshTrickleReset();
  meshSendBeaconNow(MESH_RANK_UNROUTED, meshBeaconIntervalMs);
  meshLastBeaconMs = millis();
}

static void meshAdoptParent(const uint8_t* mac, const MeshBeacon* b, int rssi, uint32_t now) {
  uint32_t interval = b->beacon_interval_ms ? b->beacon_interval_ms
                                            : MESH_BEACON_INTERVAL_MIN_MS;
  if (!meshSetParent(mac, b->rank, interval)) return;
  meshParentLastHeardMs = now;
  meshParentRssi        = rssi;
  meshTxFailCount       = 0;
  meshTrickleReset();
  char m[13]; meshFormatMac(mac, m);
  Serial.printf("[mesh] parent=%s (rank %d, rssi %d) — my rank now %d\n",
                m, b->rank, rssi, meshMyRank);
}

// ── Beacon RX (verify nettag, then parent selection) ─────────────────────────
static void meshHandleBeacon(const uint8_t* srcMac, const MeshBeacon* b,
                             int rssi, uint32_t now) {
  if (!meshLoadKeys()) return;

  // Verify nettag — a node without the NetKey cannot forge this, so it can
  // never be adopted as parent. Replaces the old encrypted-peer rejection.
  uint8_t expect[MESH_NETTAG_LEN];
  if (!meshCmacTruncated(meshNetKey, (const uint8_t*)b,
                         sizeof(MeshBeacon) - MESH_NETTAG_LEN, expect)) return;
  uint8_t diff = 0;
  for (int i = 0; i < MESH_NETTAG_LEN; i++) diff |= expect[i] ^ b->tag[i];
  if (diff != 0) { Serial.println("[mesh] beacon failed nettag — ignored"); return; }

#ifdef MESH_TEST_IGNORE_BRIDGE
  // TEST-ONLY: only the bridge advertises rank 0
  if (b->rank == 0) return;
#endif

  if (!meshNeighbors[0].used) meshTrickleReset();  // first neighbor seen
  meshNoteNeighbor(srcMac, now);

  if (b->flags & MESH_FLAG_SLEEPY) {
    if (meshHasParent_ && meshMacEqual(meshParentMac, srcMac))
      meshDropParent("parent became sleepy");
    return;
  }

  if (meshHasParent_ && meshMacEqual(meshParentMac, srcMac)) {
    // Current parent: refresh liveness
    meshParentLastHeardMs = now;
    meshParentRssi        = rssi;
    meshParentIntervalMs  = b->beacon_interval_ms ? b->beacon_interval_ms
                                                  : MESH_BEACON_INTERVAL_MIN_MS;
    meshTxFailCount       = 0;
    meshWindowDurationMs  = b->window_duration_ms;
    if (b->rank == MESH_RANK_UNROUTED) { meshDropParent("parent became unrouted"); return; }
    if ((uint8_t)(b->rank + 1) != meshMyRank) {
      meshParentRank = b->rank;
      meshMyRank     = b->rank + 1;
      meshTrickleReset();
      Serial.printf("[mesh] parent rank changed — my rank now %d\n", meshMyRank);
    }
    return;
  }

  // Strict rank rule: only a strictly lower rank may be a parent candidate.
  if (b->rank >= meshMyRank) return;
  if (meshHasParent_ &&
      !(b->rank < meshParentRank ||
        (b->rank == meshParentRank && rssi > meshParentRssi))) return;
  meshAdoptParent(srcMac, b, rssi, now);
}

static void meshCheckParentTimeout(uint32_t now) {
  if (!meshHasParent_) return;
  if (now - meshParentLastHeardMs >
      (uint32_t)MESH_PARENT_TIMEOUT_FACTOR * meshParentIntervalMs)
    meshDropParent("beacon timeout");
}

// Backstop for a parent that dies mid-trickle: 3 consecutive tx failures drop it.
static void meshNotifyTxStatus(bool ok) {
  if (ok) return;
  if (++meshTxFailCount >= 3) {
    meshTxFailCount = 0;
    if (meshHasParent_) meshDropParent("3 consecutive tx failures");
  }
}

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

// ── Data path ─────────────────────────────────────────────────────────────────
static bool meshUnicastToParent(const uint8_t* pkt) {
  if (!meshHasParent_) return false;
  return esp_now_send(meshParentMac, pkt, MESH_PACKET_LEN) == ESP_OK;
}

static void meshBufferPush(const uint8_t* pkt) {
  int tail = (meshBufHead + meshBufCount) % MESH_DATA_BUFFER_SIZE;
  memcpy(meshBuf[tail], pkt, MESH_PACKET_LEN);
  if (meshBufCount < MESH_DATA_BUFFER_SIZE) meshBufCount++;
  else meshBufHead = (meshBufHead + 1) % MESH_DATA_BUFFER_SIZE;  // oldest dropped
}

static void meshFlushBuffer() {
  while (meshBufCount > 0 && meshHasParent_) {
    if (!meshUnicastToParent(meshBuf[meshBufHead])) break;
    meshBufHead = (meshBufHead + 1) % MESH_DATA_BUFFER_SIZE;
    meshBufCount--;
  }
  if (meshBufCount == 0) meshBufHead = 0;
}

// SensorReading struct — holds the three measurements for meshSendReading.
typedef struct { float temperature; float humidity; float soil_moisture; } SensorReading;

// Build the sealed 61-byte packet and send it (or buffer while unrouted).
static void meshSendReading(const SensorReading* r) {
  if (!meshLoadKeys()) {
    Serial.println("[mesh] keys not loaded — reading dropped");
    return;
  }

  uint8_t body[MESH_BODY_LEN];
  meshPackBody(body, r->temperature, r->humidity, r->soil_moisture,
               meshBatteryMv, meshHasParent_ ? meshParentMac : MESH_BCAST,
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
  memcpy(meshLastPkt, packet, MESH_PACKET_LEN);
  meshLastPktValid = true;

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

// ── Provisioning (Task 9) ─────────────────────────────────────────────────────
static void meshSendJoinBeacon() {
  MeshJoinBeacon j = { MESH_MAGIC_V2, MESH_JOIN_MARKER, { 0 } };
  memcpy(j.mac, meshSelfMac, 6);
  esp_now_send(MESH_BCAST, (const uint8_t*)&j, sizeof(j));
}

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

// ── RTC-persistent state (deep-sleep wake cycles) ─────────────────────────────
#define MESH_RTC_MAGIC 0x47534C51UL  // 'GSLQ' — bumped from v1's GSLP on format change

typedef struct {
  uint32_t magic;
  uint16_t dataSeq;
  uint16_t beaconSeq;
  bool     hasParent;
  uint8_t  parentMac[6];   // MAC-based parent hint (replaces index)
  uint8_t  parentRank;
  uint8_t  channel;
  uint8_t  bufCount;
  uint8_t  bufHead;
  uint8_t  buf[MESH_DATA_BUFFER_SIZE][MESH_PACKET_LEN];  // sealed packets
} MeshRtcState;

RTC_DATA_ATTR static MeshRtcState meshRtcState;

static uint8_t meshRtcSavedChannel() {
  if (meshRtcState.magic != MESH_RTC_MAGIC) return 0;
  return (meshRtcState.channel >= 1 && meshRtcState.channel <= 14)
             ? meshRtcState.channel : 0;
}

static bool meshRtcRestore() {
  bool magicOk   = (meshRtcState.magic == MESH_RTC_MAGIC);
  bool timerWake = (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER);

  if (magicOk) {
    meshDataSeq   = meshRtcState.dataSeq;
    meshBeaconSeq = meshRtcState.beaconSeq;
  }
  if (!magicOk || !timerWake) return false;

  if (meshRtcState.bufCount <= MESH_DATA_BUFFER_SIZE &&
      meshRtcState.bufHead  <  MESH_DATA_BUFFER_SIZE) {
    meshBufCount = meshRtcState.bufCount;
    meshBufHead  = meshRtcState.bufHead;
    memcpy(meshBuf, meshRtcState.buf, sizeof(meshBuf));
  }

  if (meshRtcState.hasParent &&
      meshRtcState.parentRank != MESH_RANK_UNROUTED) {
    if (meshSetParent(meshRtcState.parentMac, meshRtcState.parentRank,
                      MESH_BEACON_INTERVAL_MAX_MS)) {
      meshParentRssi = -128;   // unknown until a fresh beacon is heard
      return true;
    }
  }
  return false;
}

static void meshRtcPersist(uint8_t channel) {
  meshRtcState.magic     = MESH_RTC_MAGIC;
  meshRtcState.dataSeq   = meshDataSeq;
  meshRtcState.beaconSeq = meshBeaconSeq;
  meshRtcState.hasParent = meshHasParent_;
  memcpy(meshRtcState.parentMac, meshParentMac, 6);
  meshRtcState.parentRank = meshParentRank;
  meshRtcState.channel    = channel;
  meshRtcState.bufCount   = (uint8_t)meshBufCount;
  meshRtcState.bufHead    = (uint8_t)meshBufHead;
  memcpy(meshRtcState.buf, meshBuf, sizeof(meshBuf));
}

static void meshRequeueLastReading() {
  if (!meshLastPktValid) return;
  meshBufferPush(meshLastPkt);
  meshLastPktValid = false;
}

// ── Relay data path ───────────────────────────────────────────────────────────
static void meshRelayData(const uint8_t* srcMac, const uint8_t* data, int len) {
  if (len != MESH_PACKET_LEN) return;
  if (data[0] != MESH_MAGIC_V2) return;
  if (!meshLoadKeys()) return;

  // Cheap nettag check — drop stranger's garbage without decrypting.
  if (!meshVerifyNettag(data, meshNetKey)) return;

  // ttl is outside the nettag — bound it defensively.
  uint8_t ttl = data[15];
  if (ttl > MESH_MAX_TTL) return;

  // Parse origin MAC (bytes 1..6) and seq (bytes 7..8 LE).
  const uint8_t* originMac = data + 1;
  uint16_t seq = (uint16_t)(data[7] | ((uint16_t)data[8] << 8));

  if (meshDedupSeen(originMac, seq)) {
    Serial.println("[mesh] duplicate — dropped");
    return;
  }
  if (!meshHasParent_) { Serial.println("[mesh] relay with no parent — dropped"); return; }
  if (ttl == 0) { Serial.println("[mesh] ttl expired — dropped"); return; }

  // Decrement ttl in-place (it is NOT part of either tag — that's the point).
  uint8_t fwd[MESH_PACKET_LEN];
  memcpy(fwd, data, MESH_PACKET_LEN);
  fwd[15]--;
  meshUnicastToParent(fwd);
  char m[13]; meshFormatMac(originMac, m);
  Serial.printf("[mesh] relayed packet from %s (ttl now %d)\n", m, fwd[15]);
}
