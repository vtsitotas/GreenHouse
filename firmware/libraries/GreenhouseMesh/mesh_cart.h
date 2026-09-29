// firmware/libraries/GreenhouseMesh/mesh_cart.h
#pragma once
// CART depth N — the per-wake cycle of a sleepy node that also relays.
// Spec: docs/superpowers/specs/2026-09-28-cart-depth-n-design.md
//
// One wake per cycle. A node:
//   1. opens its receive window (RX_OPEN beacons every 100 ms) for its children,
//      warming its sensors inside that window;
//   2. at the end of the window catches its parent's RX_OPEN beacon (the parent's
//      window follows ours: the "ladder"), re-anchoring its clock on it;
//   3. forwards its own readings + everything its children handed over, inside
//      the parent's window, keeping a frame until the parent's L2 ACK (custody);
//   4. sleeps until one slot before the parent's next predicted window.
// A node whose parent is always-on (the bridge, a mains node) runs free: window,
// then send. An orphan sweeps (listens a whole cycle) for any adoptable parent.
#include <esp_random.h>
#include "mesh_node.h"
#include "mesh_sched.h"

typedef struct {
  void (*sensorsPower)(bool on);
  void (*readSensors)(SensorReading* out);   // fills *out and calls meshSetBatteryMv()
  uint32_t warmupMs;
} MeshCartHooks;

#define MESH_CART_RTC_MAGIC 0x43415254UL     // 'CART'
RTC_DATA_ATTR static uint32_t       meshSchedMagic;
RTC_DATA_ATTR static uint8_t        meshSchedParent[6];
RTC_DATA_ATTR static MeshSchedState meshSched;
RTC_DATA_ATTR static uint32_t       meshCartParentCycle;   // parent's advertised T
RTC_DATA_ATTR static uint8_t        meshCartSweepFails;    // back-off after empty sweeps
RTC_DATA_ATTR static uint8_t        meshCartTxFailCycles;  // always-on parent unreachable

static uint32_t meshCartLastBeaconMs = 0;

// A child follows its sleepy parent's period so mixed builds still rendezvous.
static uint32_t meshCycleMs() {
  if (meshHasParent_ && meshParentSleepy &&
      meshCartParentCycle >= MESH_CYCLE_MIN_MS && meshCartParentCycle <= MESH_CYCLE_MAX_MS)
    return meshCartParentCycle;
  return MESH_SLEEP_INTERVAL_MS;
}

static MeshSchedCfg meshCartSchedCfg(uint32_t cycleMs) {
  MeshSchedCfg c;
  c.gMinMs = MESH_WAKE_GUARD_MIN_MS;
  c.gMaxMs = meshSchedGuardMax(cycleMs, MESH_DRIFT_BIAS_PPM, MESH_DRIFT_STEP_PPM_300S, MESH_GUARD_CAP_MS);
  if (c.gMaxMs < c.gMinMs) c.gMaxMs = c.gMinMs;
  c.padMs = 50; c.kNum = 3; c.kDen = 2;
  return c;
}

// Scheduler state is only meaningful for the parent it was learned against.
static void meshCartSchedValidate(const MeshSchedCfg* c) {
  if (meshSchedMagic != MESH_CART_RTC_MAGIC || !meshMacEqual(meshSchedParent, meshParentMac)) {
    meshSchedReset(&meshSched, c);
    memcpy(meshSchedParent, meshParentMac, 6);
    meshSchedMagic = MESH_CART_RTC_MAGIC;
  }
  if (meshSched.guardMs > c->gMaxMs) meshSched.guardMs = c->gMaxMs;   // T changed
}

// ── our receive window ───────────────────────────────────────────────────────
static void meshCartServiceWindow(uint32_t cycleMs, bool force) {
  if (!meshRxWindowOpen) return;
  uint32_t now = millis();
  if (!force && now - meshCartLastBeaconMs < MESH_RX_BEACON_PERIOD_MS) return;
  meshCartLastBeaconMs = now;
  // Full buffer: tell our children to hold, and stop accepting new ones.
  uint8_t f = MESH_FLAG_RX_OPEN | (meshRelayFull() ? MESH_FLAG_BUF_FULL : MESH_FLAG_RELAY_CAP);
  meshSendBeaconEx(meshMyRank, cycleMs, f, now - meshRxOpenedMs);
}

static void meshCartOpenWindow(uint32_t cycleMs) {
  if (!meshIsSelfRelayCapable() || !meshHasParent_) return;   // leaf-only / unrouted
  meshRxOpenedMs = millis();
  meshRxWindowOpen = true;
  meshCartServiceWindow(cycleMs, true);
}

static void meshCartCloseWindow() { meshRxWindowOpen = false; }

// ── custody transfer ─────────────────────────────────────────────────────────
static void meshCartSealOwn(const SensorReading* r) {
  if (!meshLoadKeys()) return;
  uint8_t body[MESH_BODY_LEN];
  meshPackBody(body, r->temperature, r->humidity, r->soil_moisture, meshBatteryMv,
               meshHasParent_ ? meshParentMac : MESH_BCAST, (int8_t)meshParentRssi);
  uint8_t packet[MESH_PACKET_LEN];
  if (meshSeal(packet, meshAppKey, meshNetKey, meshSelfMac, meshDataSeq++,
               meshStoreBootCount(), MESH_FLAG_SLEEPY, meshMyRank, meshTxTtl(), body))
    meshBufferPush(packet);
}

static void meshCartWait(uint32_t ms, uint32_t untilMs) {
  uint32_t t = millis();
  while (millis() - t < ms && (int32_t)(untilMs - millis()) > 0) delay(1);
}

// One frame, same bytes on every attempt (a resend must de-dup, never re-seal).
static bool meshCartSendOne(uint8_t* pkt, uint32_t untilMs) {
  for (int a = 0; a < MESH_CART_ATTEMPTS && (int32_t)(untilMs - millis()) > 0; a++) {
    if (a) meshCartWait(meshSchedJitterMs(MESH_CART_JITTER_MS, esp_random()), untilMs);
    meshLastTxStatus = -1;
    if (!meshUnicastToParent(pkt)) continue;
    uint32_t t = millis();
    while (meshLastTxStatus == -1 && millis() - t < MESH_TX_CONFIRM_WAIT_MS) delay(1);
    if (meshLastTxStatus == 1) return true;
  }
  return false;
}

// Own readings (oldest first), then relayed frames. A frame leaves a buffer only
// after the parent's L2 ACK; the first undeliverable frame stops the flush and
// everything left stays in RTC memory for the next cycle.
static int meshCartFlush(uint32_t untilMs) {
  int sent = 0;
  while (meshBufCount > 0 && (int32_t)(untilMs - millis()) > 0) {
    meshBuf[meshBufHead][15] = meshTxTtl();          // TTL at transmit time (outside both tags)
    if (!meshCartSendOne(meshBuf[meshBufHead], untilMs)) return sent;
    meshBufHead = (meshBufHead + 1) % MESH_DATA_BUFFER_SIZE;
    meshBufCount--;
    sent++;
  }
  if (meshBufCount == 0) meshBufHead = 0;
  while (meshRelayCount > 0 && (int32_t)(untilMs - millis()) > 0) {
    if (!meshCartSendOne(meshRelayBuf[meshRelayHead], untilMs)) return sent;
    meshRelayPop();
    sent++;
  }
  return sent;
}

static void meshCartNoteParentCycle() {
  if (meshParentIntervalMs >= MESH_CYCLE_MIN_MS && meshParentIntervalMs <= MESH_CYCLE_MAX_MS)
    meshCartParentCycle = meshParentIntervalMs;
}

// Inside the parent's open window [parentOpen, parentOpen + SLOT]: send after a jitter.
static void meshCartSendInParentWindow(uint32_t parentOpenMs) {
  uint32_t winEnd = parentOpenMs + MESH_CART_SLOT_MS;
  if (meshCatchBufFull) {
    Serial.println("[cart] parent buffer full — holding frames");
    return;
  }
  meshCartWait(meshSchedJitterMs(MESH_CART_JITTER_MS, esp_random()), winEnd);
  int n = meshCartFlush(winEnd);
  Serial.printf("[cart] sent %d, left: own %d relay %u\n", n, meshBufCount, meshRelayCount);
}

// ── the cycle ────────────────────────────────────────────────────────────────
// Returns how long to deep-sleep. The sketch persists RTC state and sleeps.
static uint32_t meshCartCycle(const MeshCartHooks* h, bool timerWake) {
  meshCartActive = true;
  meshRelayBegin(timerWake);
  if (!timerWake) { meshCartSweepFails = 0; meshCartTxFailCycles = 0; }
  const uint32_t w0 = millis();
  uint32_t cycle = meshCycleMs();
  MeshSchedCfg cfg = meshCartSchedCfg(cycle);
  meshCartSchedValidate(&cfg);

  h->sensorsPower(true);
  bool haveReading = false;
  SensorReading r;
  auto service = [&]() {
    meshCartServiceWindow(cycle, false);
    if (!haveReading && millis() - w0 >= h->warmupMs) {
      h->readSensors(&r);
      h->sensorsPower(false);
      haveReading = true;
      meshCartSealOwn(&r);
    }
  };
  auto finishReading = [&](uint32_t untilMs) {
    while (!haveReading && (int32_t)(untilMs - millis()) > 0) { service(); delay(2); }
  };
  auto done = [&](uint32_t sleepMs) -> uint32_t {
    meshCartCloseWindow();
    if (!haveReading) { h->readSensors(&r); h->sensorsPower(false); meshCartSealOwn(&r); }
    meshCartActive = false;
    Serial.printf("[cart] awake %lums, sleeping %lums (G=%u delta=%ld)\n",
                  (unsigned long)(millis() - w0), (unsigned long)sleepMs,
                  meshSched.guardMs, (long)meshSched.deltaMs);
    return sleepMs < MESH_MIN_SLEEP_MS ? MESH_MIN_SLEEP_MS : sleepMs;
  };

  // ── orphan: sweep a whole cycle for any adoptable parent ──────────────────
  if (!meshHasParent()) {
    meshSendBeaconNow(MESH_RANK_UNROUTED, cycle);   // always-on neighbours answer at once
    meshCatchSeen = false;
    uint32_t end = w0 + cycle + MESH_CART_SLOT_MS;
    while (!meshHasParent() && (int32_t)(end - millis()) > 0) { service(); delay(5); }
    if (!meshHasParent()) {
      uint8_t k = meshCartSweepFails < 3 ? ++meshCartSweepFails : 3;
      Serial.printf("[cart] sweep found no parent (%u in a row)\n", k);
      return done(cycle * (1u << k));                // back off: 2, 4, 8 cycles
    }
    meshCartSweepFails = 0;
    if (meshParentSleepy && meshCatchSeen) {          // adopted inside its window
      cfg = meshCartSchedCfg(meshCycleMs());
      meshCartSchedValidate(&cfg);
      meshSchedOnAnchor(&meshSched, &cfg);
      meshCartNoteParentCycle();
      uint32_t parentOpen = meshCatchOpenMs;
      finishReading(parentOpen + MESH_CART_SLOT_MS / 2);
      meshCartSendInParentWindow(parentOpen);
      return done(meshSchedSleepMs(&meshSched, parentOpen, millis(), meshCycleMs(),
                                   MESH_CART_SLOT_MS, MESH_MIN_SLEEP_MS));
    }
    finishReading(millis() + h->warmupMs);            // always-on parent: send now
    meshCartFlush(millis() + MESH_CART_SLOT_MS);
    return done(cycle - (millis() - w0) % cycle);
  }

  // ── parent always on (bridge / mains node): free-running ───────────────────
  if (!meshParentSleepy) {
    uint32_t open = millis();
    meshCartOpenWindow(cycle);
    while (millis() - open < MESH_CART_SLOT_MS) { service(); delay(2); }
    meshCartCloseWindow();
    finishReading(millis() + h->warmupMs);
    int before = meshBufCount + meshRelayCount;
    int n = meshCartFlush(millis() + MESH_CART_SLOT_MS);
    if (before > 0 && n == 0) {
      if (++meshCartTxFailCycles >= 3) { meshCartTxFailCycles = 0; meshDropParent("always-on parent unreachable"); }
    } else {
      meshCartTxFailCycles = 0;
    }
    Serial.printf("[cart] sent %d to always-on parent, left own %d relay %u\n", n, meshBufCount, meshRelayCount);
    return done(w0 + cycle - millis());
  }

  // ── parent sleepy: the ladder ─────────────────────────────────────────────
  const uint32_t ownOpen    = w0 + meshSchedOwnOpenMs(&meshSched, MESH_CART_SLOT_MS);
  const uint32_t predParent = w0 + meshSchedPredParentOpenMs(&meshSched, MESH_CART_SLOT_MS);
  const uint32_t deadline   = w0 + meshSchedCatchDeadlineMs(&meshSched, MESH_CART_SLOT_MS);
  meshCatchSeen = false;
  bool opened = false;
  while (!meshCatchSeen && (int32_t)(deadline - millis()) > 0) {
    if (!opened && (int32_t)(millis() - ownOpen) >= 0) { meshCartOpenWindow(cycle); opened = true; }
    service();
    delay(2);
  }
  meshCartCloseWindow();

  if (!meshCatchSeen) {
    uint16_t g = meshSched.guardMs;
    MeshSchedAction a = meshSchedOnMiss(&meshSched, &cfg);
    Serial.printf("[cart] missed parent window (G=%u, run=%u, action=%d)\n", g, meshSched.missRun, (int)a);
    if (a == MESH_SCHED_SWEEP) {
      meshDropParent("missed 3 parent windows");
      return done(MESH_MIN_SLEEP_MS);                 // next wake is an orphan sweep
    }
    // Virtual anchor = where the parent was predicted; the error carries over.
    return done(meshSchedSleepMs(&meshSched, predParent, millis(), cycle,
                                 MESH_CART_SLOT_MS, MESH_MIN_SLEEP_MS));
  }

  uint32_t parentOpen = meshCatchOpenMs;
  if (!meshSched.anchored) meshSchedOnAnchor(&meshSched, &cfg);
  else meshSchedOnCatch(&meshSched, &cfg, (int32_t)(parentOpen - predParent));
  meshCartNoteParentCycle();
  finishReading(parentOpen + MESH_CART_SLOT_MS / 2);
  meshCartSendInParentWindow(parentOpen);
  return done(meshSchedSleepMs(&meshSched, parentOpen, millis(), meshCycleMs(),
                               MESH_CART_SLOT_MS, MESH_MIN_SLEEP_MS));
}
