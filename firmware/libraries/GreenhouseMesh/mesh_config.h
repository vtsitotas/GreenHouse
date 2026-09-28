#pragma once
// ── GreenhouseMesh: network-wide configuration ────────────────────────────────
// Single source of truth for every node (bridge + both edge variants).
// v2: TRUSTED_NODES[] and per-device encrypted-peer model removed. The 8-node
// ceiling it imposed is gone. Nodes are enrolled at runtime via the app; the
// trust store lives on the Pi (nodes.json). Add any number of sensors.

#include <stdint.h>

// ── Protocol ──────────────────────────────────────────────────────────────────
#define MESH_MAGIC          0x47   // 'G' — v1 magic (old packets, now rejected)
#define MESH_MAGIC_V2       0x48   // 'H' — v2 magic with AES-GCM sealing
#define MESH_RANK_UNROUTED  255    // sentinel: node has no valid parent
#define MESH_TTL_MARGIN     2      // adaptive TTL = origin's own rank + this margin
                                   // (see meshSendReading()) — covers small
                                   // parent-rank drift while the packet is in
                                   // flight, without capping how deep the mesh
                                   // can physically grow
#define MESH_MAX_TTL        64     // ceiling/fallback only: used when a reading
                                   // (was 16, which silently capped delivery at
                                   // rank 17 because meshTxTtl() clamps rank+2 to
                                   // it — see spec 2026-09-28-cart-depth-n. Must
                                   // match ACK_TTL_MAX in pi/scripts/serial_bridge.py.)
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
#define MESH_ORPHAN_FRESH_MS         60000UL  // an UNROUTED beacon from a MAC not
                                              // heard for this long is a new orphan
#define MESH_ORPHAN_RESET_MIN_GAP_MS 10000UL  // at most one orphan-triggered
                                              // trickle reset per 10 s

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
#define MESH_APP_ACK_WAIT_MS      2000UL    // wait for the Pi's real accept/reject
                                            // ack after L2 delivery succeeds —
                                            // covers one UART round trip plus a
                                            // few mesh hops each way, comfortably
                                            // under MESH_WAKE_MAX_AWAKE_MS
#define MESH_WAKE_MAX_AWAKE_MS    10000UL   // hard backstop: persist state and
                                            // sleep no matter what path we're on
#define MESH_MIN_SLEEP_MS         1000UL    // floor after subtracting awake time
                                            // (never arm a 0/negative timer)

// ── CART depth N: every node sleeps AND relays (spec 2026-09-28-cart-depth-n) ──
// Ladder schedule: a node's receive window for its children sits right before
// its parent's window. Constants marked "Gate 0" are provisional until the
// drift bench (run 2) measures them; the rest are meshsim-informed best guesses.
#define MESH_CART_ENABLE          1         // 0 = exact Phase 1 behaviour (rollback)
#define MESH_FLAG_RX_OPEN         0x02      // beacon: sender's receive window is open now
#define MESH_FLAG_RELAY_CAP       0x04      // beacon: sender accepts children
#define MESH_FLAG_BUF_FULL        0x08      // beacon: relay buffer full, keep your frames
#define MESH_CART_SLOT_MS         2500UL    // receive window per node; >= sensor warm-up
                                            // (2000 ms) so the own reading is ready to send
#define MESH_CART_JITTER_MS       300UL     // random send delay after a catch (meshsim:
                                            // 100 ms -> ~50 % collisions at 10 nodes/rank)
#define MESH_CART_ATTEMPTS        3         // L2 send attempts per frame per window
#define MESH_RX_BEACON_PERIOD_MS  100UL     // RX_OPEN beacon cadence inside the window
#define MESH_WAKE_GUARD_MIN_MS    250       // radio/boot jitter floor
#define MESH_DRIFT_BIAS_PPM       1700UL    // Gate 0 run 1: worst pair relative bias
#define MESH_DRIFT_STEP_PPM_300S  100UL     // Gate 0: per-cycle wander step (planning value)
#define MESH_GUARD_CAP_MS         20000UL   // hard ceiling for G_max
#define MESH_RELAY_BUFFER_SIZE    50        // relayed frames kept in RTC across sleep
                                            // (50 x 61 B = 3050 B of the 8 KB RTC FAST)
#define MESH_CYCLE_MIN_MS         30000UL   // sanity bounds on a parent's advertised period
#define MESH_CYCLE_MAX_MS         3600000UL

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
