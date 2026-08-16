#pragma once
// ── GreenhouseMesh: network-wide configuration ────────────────────────────────
// Single source of truth for every node (bridge + both edge variants).
// Supersedes the bridge's old ZONES[] array. Adding a node = add its MAC/zone
// here and reflash the fleet (same process as ZONES[] before — spec Non-goals).

#include <stdint.h>

// ── Protocol ──────────────────────────────────────────────────────────────────
#define MESH_MAGIC          0x47   // 'G' — version/sanity marker on every packet
#define MESH_RANK_UNROUTED  255    // sentinel: node has no valid parent
#define MESH_TTL_MARGIN     2      // adaptive TTL = origin's own rank + this margin
                                   // (see meshSendReading()) — covers small
                                   // parent-rank drift while the packet is in
                                   // flight, without capping how deep the mesh
                                   // can physically grow
#define MESH_MAX_TTL        16     // ceiling/fallback only: used when a reading
                                   // is buffered while unrouted (rank not known
                                   // yet, see meshSendReading()) and as a hard
                                   // backstop against runaway forwarding. Loops
                                   // are structurally prevented by the
                                   // strict-rank rule regardless — this is
                                   // defense in depth only, not the primary
                                   // hop limit anymore.

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

// ── Fixed channel (UART-bridge / no-router deployments) ───────────────────────
// The default deployment scans for a home router's SSID purely to agree on a
// channel — no node ever actually joins that WiFi. In a deployment with no
// router at all (bridge wired directly to the Pi over UART instead of WiFi,
// see docs/superpowers/specs/2026-07-20-uart-bridge-design.md), there's
// nothing to scan for, so every node — bridge included — locks to this
// constant instead. Change it if 2.4GHz channel 1 is noisy on site; every
// node in the fleet must be reflashed together if it does (same one-shot
// reflash requirement as any other mesh_config.h change).
#define MESH_FIXED_CHANNEL  1

// ── Deep sleep (Phase 1: leaf sleep — spec 2026-07-26-mesh-deep-sleep) ────────
#define MESH_FLAG_SLEEPY          0x01      // beacon/data flags bit: sender is a
                                            // battery node — NEVER adopt as parent
#define MESH_SLEEP_INTERVAL_MS    60000UL   // 1 min test duty cycle (change back to 900000UL for 15 min production)
#define MESH_WAKE_DISCOVERY_MS    5000UL    // orphaned-wake listen window before
                                            // giving up and buffering the reading
#define MESH_TX_CONFIRM_WAIT_MS   500UL     // wait for the ESP-NOW send callback
                                            // before judging a unicast delivered
#define MESH_WAKE_MAX_AWAKE_MS    10000UL   // hard backstop: persist state and
                                            // sleep no matter what path we're on
#define MESH_MIN_SLEEP_MS         1000UL    // floor after subtracting awake time
                                            // (never arm a 0/negative timer)

// ── Buffers ───────────────────────────────────────────────────────────────────
#define MESH_DEDUP_CACHE_SIZE  32   // (origin_mac, seq) ring — drops route-flap dupes
#define MESH_DEDUP_WINDOW_MS   30000UL  // how long a (mac, seq) counts as "already
                                    // seen". seq only identifies a reading while its
                                    // origin keeps RTC state across sleep; any power
                                    // loss (dead pack, brownout, battery swap)
                                    // restarts it at 0, so entries MUST expire or the
                                    // bridge rejects that node's every later reading
                                    // until someone reboots the bridge. Keep above
                                    // MESH_WAKE_MAX_AWAKE_MS (a wake cycle re-sends
                                    // the SAME seq after an unconfirmed tx, and that
                                    // retry must still de-dup) and below
                                    // MESH_SLEEP_INTERVAL_MS (a restarted node's
                                    // recycled seq must have expired by its next wake).
#define MESH_DATA_BUFFER_SIZE  10   // own readings buffered while isolated
                                    // (most-recent 10, oldest dropped, RAM only)

// Both bounds on the de-dup window are load-bearing, and MESH_SLEEP_INTERVAL_MS
// is routinely retuned (test vs production duty cycle) — enforce them at compile
// time instead of trusting the comment above to be re-read.
static_assert(MESH_DEDUP_WINDOW_MS > MESH_WAKE_MAX_AWAKE_MS,
              "MESH_DEDUP_WINDOW_MS must outlast one wake cycle, or a wake's own "
              "same-seq retry stops being de-duped");
static_assert(MESH_DEDUP_WINDOW_MS < MESH_SLEEP_INTERVAL_MS,
              "MESH_DEDUP_WINDOW_MS must expire before a restarted node's next wake, "
              "or that node's recycled seq is dropped as a duplicate forever");

// ── Bridge offline detection ──────────────────────────────────────────────────
#define MESH_OFFLINE_AFTER               3       // x expected report interval
#define MESH_EXPECTED_REPORT_INTERVAL_MS 5000UL  // matches SEND_INTERVAL_MS on edges

// ── Keys (16 bytes each, network-wide — spec Non-goals: no per-pair keys) ─────
// Shared-key model: defends against a nearby stranger device injecting/reading
// data; does NOT defend against key extraction from a captured node.
// Random 128-bit key material (openssl rand -hex 16) — NOT human-readable
// text. A readable placeholder string here would be low-entropy and
// guessable, defeating the point of turning encryption on at all.
static const uint8_t MESH_PMK[16] =
  { 0x5C, 0x1B, 0x28, 0x53, 0x4F, 0x68, 0x9B, 0x34,
    0xD4, 0xEB, 0xE2, 0x91, 0x49, 0xD5, 0xFA, 0x26 };
static const uint8_t MESH_LMK[16] =
  { 0xD4, 0xEB, 0xFC, 0x75, 0x61, 0xF6, 0x42, 0x3F,
    0xF9, 0x07, 0x1E, 0x2D, 0xB5, 0xC8, 0x39, 0x83 };

// ── Trusted nodes ─────────────────────────────────────────────────────────────
// Every real device in the network, bridge included (zone = nullptr for the
// bridge). A MAC not in this list is ignored as a routing candidate and has no
// encrypted-peer relationship, so its data unicasts can't even decrypt.
// LIMIT: keep at most 8 entries — ESP-NOW allows 7 encrypted peers per node
// (each node registers every entry except itself).
struct TrustedNode {
  uint8_t     mac[6];
  const char* zone;   // MQTT zone name, or nullptr for the bridge
  bool        sleepy; // battery node: leaf-only, deep-sleeps between readings.
                      // Role is looked up by each node's OWN MAC at boot, so a
                      // single edge firmware image serves both roles. The bridge
                      // uses it for per-role offline windows (sleepy nodes are
                      // expected only every MESH_SLEEP_INTERVAL_MS).
};

static const TrustedNode TRUSTED_NODES[] = {
  { { 0x20, 0x6E, 0xF1, 0x6C, 0xBE, 0x80 }, nullptr, false },  // bridge (ESP32-C3, unchanged)
  // zone1 (A1:B0) pulled 2026-08-16: confirmed bad TX antenna — hears the
  // bridge fine (rssi -56..-67) but 0/7 unicasts ever got a MAC-layer ACK,
  // even at point-blank range. Re-add once the antenna/board is fixed.
  // sleepy=false 2026-08-16 for a real relay test (see docs/DEVICES.md) — a
  // sleepy node can never be adopted as a parent (mesh_node.h
  // meshHandleBeacon), so proving multi-hop relay requires at least one
  // always-on node in the chain. All 3 flipped for now; flip back to true
  // before any real deployment run, since always-on draws mains-level
  // current (~86mA active, no deep-sleep floor) and won't survive on battery.
  { { 0x20, 0x6E, 0xF1, 0x6C, 0x9D, 0xB0 }, "zone2", false },  // ESP32-C3 edge node (mains, relay test)
  { { 0x20, 0x6E, 0xF1, 0x6C, 0x6B, 0x50 }, "zone3", false },  // ESP32-C3 edge node (mains, relay test)
  { { 0x20, 0x6E, 0xF1, 0x6C, 0x75, 0xEC }, "zone4", false },  // ESP32-C3 edge node (mains, relay test)
};
static const int TRUSTED_NODE_COUNT = sizeof(TRUSTED_NODES) / sizeof(TRUSTED_NODES[0]);
