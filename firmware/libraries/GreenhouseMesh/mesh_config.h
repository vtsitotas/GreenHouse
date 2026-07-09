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
