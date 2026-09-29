// firmware/libraries/GreenhouseMesh/mesh_sched.h
#pragma once
// CART ladder schedule arithmetic -- pure, no Arduino/ESP-IDF includes, so it
// is unit-tested on a host (firmware/test/host/sched_tests.cpp).
// Spec: docs/superpowers/specs/2026-09-28-cart-depth-n-design.md (ladder) and
// 2026-09-23-cart-v2-revision.md §3.3 (guard policy).
//
// Ladder, per node and per cycle (times from this wake's start):
//   early                        own receive window opens (children send)
//   early + slot                 parent's window predicted to open (we send)
//   early + slot + G/2           give up: missed the parent this cycle
// early = max(0, G/2 − slot): the part of the guard our own window does not
// already cover (we are awake and listening during our window anyway).
#include <stdint.h>

#define MESH_SCHED_HIST 16

typedef struct {
  uint16_t gMinMs, gMaxMs, padMs;
  uint8_t  kNum, kDen;                   // margin factor k = kNum/kDen (1.5)
} MeshSchedCfg;

typedef struct {
  int32_t  deltaMs;                      // learned per-cycle offset of the parent's
                                         // period in our clock (incl. boot latency)
  uint16_t guardMs;                      // current guard window G
  uint16_t hist[MESH_SCHED_HIST];        // |err| of recent catches
  uint8_t  histN, histHead, missRun, estKnown, anchored;
} MeshSchedState;

typedef enum { MESH_SCHED_RETRY = 0, MESH_SCHED_EXTENDED = 1, MESH_SCHED_SWEEP = 2 } MeshSchedAction;

// G_max: the spec's bias rule 2·b·T·1.3 (a re-anchored child must catch its
// parent with an unknown offset) PLUS six sigma of the temperature-driven
// wander σ = T·step/√(1−0.98²), which the bias rule alone misses (meshsim:
// 0.17 % bias at T = 900 s gives 3.98 s against a 1.36 s wander σ and the pair
// ends up in a miss/sweep loop). step scales with T (step_ppm = s300·T/300 s).
static inline uint16_t meshSchedGuardMax(uint32_t cycleMs, uint32_t biasPpm,
                                         uint32_t stepPpmPer300s, uint32_t capMs) {
  uint64_t bias = (uint64_t)2 * cycleMs * biasPpm * 13 / 10 / 1000000u;
  uint64_t stepPpm = (uint64_t)stepPpmPer300s * cycleMs / 300000u;
  uint64_t wander = (uint64_t)6 * cycleMs * stepPpm * 1000 / (1000000ull * 199);  // √(1−0.98²)=0.199
  uint64_t g = bias > wander ? bias : wander;
  if (g > capMs) g = capMs;
  return (uint16_t)(g > 0xFFFF ? 0xFFFF : g);
}

static inline void meshSchedReset(MeshSchedState* s, const MeshSchedCfg* c) {
  s->deltaMs = 0; s->guardMs = c->gMaxMs; s->histN = 0; s->histHead = 0;
  s->missRun = 0; s->estKnown = 0; s->anchored = 0;
}

// Timing re-anchored (sweep catch / adoption inside the parent's window);
// the per-cycle offset is unknown until the next catch measures it.
static inline void meshSchedOnAnchor(MeshSchedState* s, const MeshSchedCfg* c) {
  meshSchedReset(s, c);
  s->anchored = 1;
}

static inline uint16_t meshSchedAbs16(int32_t v) {
  uint32_t a = (uint32_t)(v < 0 ? -v : v);
  return a > 0xFFFF ? 0xFFFF : (uint16_t)a;
}

// errMs = observed parent open − predicted parent open (both in this wake's clock).
static inline void meshSchedOnCatch(MeshSchedState* s, const MeshSchedCfg* c, int32_t errMs) {
  s->missRun = 0;
  if (!s->estKnown) {                    // first period observed since the anchor
    s->deltaMs += errMs;
    s->estKnown = 1;
    return;
  }
  s->deltaMs += (errMs * 3) / 10;        // EWMA, gain 0.3 (cart_sim.py)
  s->hist[s->histHead] = meshSchedAbs16(errMs);
  s->histHead = (uint8_t)((s->histHead + 1) % MESH_SCHED_HIST);
  if (s->histN < MESH_SCHED_HIST) s->histN++;
  if (s->histN == MESH_SCHED_HIST) {     // margin policy: G = 2·k·max|err| + pad
    uint32_t mx = 0;
    for (int i = 0; i < MESH_SCHED_HIST; i++) if ((uint32_t)s->hist[i] > mx) mx = s->hist[i];
    uint32_t g = 2u * mx * (uint32_t)c->kNum / (uint32_t)c->kDen + (uint32_t)c->padMs;
    if (g < (uint32_t)c->gMinMs) g = c->gMinMs;
    if (g > (uint32_t)c->gMaxMs) g = c->gMaxMs;
    s->guardMs = (uint16_t)g;
  }
}

// Missed the parent's window: double G, then G_max, then give up (sweep).
static inline MeshSchedAction meshSchedOnMiss(MeshSchedState* s, const MeshSchedCfg* c) {
  s->missRun++;
  if (s->missRun >= 3) { s->anchored = 0; return MESH_SCHED_SWEEP; }
  if (s->missRun == 2) { s->guardMs = c->gMaxMs; return MESH_SCHED_EXTENDED; }
  uint32_t g = (uint32_t)s->guardMs * 2u;
  s->guardMs = (uint16_t)(g > (uint32_t)c->gMaxMs ? (uint32_t)c->gMaxMs : g);
  return MESH_SCHED_RETRY;
}

static inline uint32_t meshSchedEarlyMs(const MeshSchedState* s, uint32_t slotMs) {
  uint32_t half = s->guardMs / 2u;
  return half > slotMs ? half - slotMs : 0;
}

static inline uint32_t meshSchedOwnOpenMs(const MeshSchedState* s, uint32_t slotMs) {
  return meshSchedEarlyMs(s, slotMs);
}

static inline uint32_t meshSchedPredParentOpenMs(const MeshSchedState* s, uint32_t slotMs) {
  return meshSchedEarlyMs(s, slotMs) + slotMs;
}

static inline uint32_t meshSchedCatchDeadlineMs(const MeshSchedState* s, uint32_t slotMs) {
  return meshSchedPredParentOpenMs(s, slotMs) + s->guardMs / 2u;
}

// Sleep so that the next wake starts `early` before our own window, which
// opens one slot before the parent's next predicted window:
// wake = parentOpen + T + delta − slot − early (all in this wake's millis).
static inline uint32_t meshSchedSleepMs(const MeshSchedState* s, uint32_t parentOpenMs, uint32_t nowMs,
                                        uint32_t cycleMs, uint32_t slotMs, uint32_t minSleepMs) {
  int64_t target = (int64_t)parentOpenMs + cycleMs + s->deltaMs - (int64_t)slotMs
                   - (int64_t)meshSchedEarlyMs(s, slotMs);
  int64_t sleep = target - (int64_t)nowMs;
  return sleep < (int64_t)minSleepMs ? minSleepMs : (uint32_t)sleep;
}

static inline uint16_t meshSchedJitterMs(uint32_t jitterMs, uint32_t rnd) {
  return jitterMs ? (uint16_t)(rnd % jitterMs) : 0;
}
