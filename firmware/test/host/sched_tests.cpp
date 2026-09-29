// firmware/test/host/sched_tests.cpp -- host unit tests for mesh_sched.h (CART ladder scheduling)
#include <cstdio>
#include "../../libraries/GreenhouseMesh/mesh_sched.h"
#include "../../libraries/GreenhouseMesh/mesh_config.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static MeshSchedCfg cfg() {
  MeshSchedCfg c;
  c.gMinMs = 250; c.gMaxMs = 8141; c.padMs = 50; c.kNum = 3; c.kDen = 2;
  return c;
}

int main() {
  MeshSchedCfg c = cfg();
  MeshSchedState s;

  // ── guard ceiling: bias rule (spec §3.3) + temperature wander (meshsim) ──
  // T = 900 s, b = 1700 ppm: bias term 2·0.0017·900·1.3 = 3.978 s;
  // step = 100 ppm·900/300 = 300 ppm → σ = 900 s·300e-6/0.199 = 1.3568 s → 6σ = 8.141 s
  CHECK(meshSchedGuardMax(900000, 1700, 100, 20000) == 8140 || meshSchedGuardMax(900000, 1700, 100, 20000) == 8141);
  CHECK(meshSchedGuardMax(900000, 1700, 0, 20000) == 3978);        // no wander → bias rule only
  CHECK(meshSchedGuardMax(3600000, 6000, 300, 20000) == 20000);    // capped
  CHECK(meshSchedGuardMax(60000, 1700, 100, 20000) == 265);        // 60 s bench build

  // ── state machine (margin policy, cart_sim.py) ──
  meshSchedReset(&s, &c);
  CHECK(s.anchored == 0 && s.estKnown == 0 && s.guardMs == 8141);
  meshSchedOnAnchor(&s, &c);
  CHECK(s.anchored == 1 && s.estKnown == 0 && s.deltaMs == 0);
  meshSchedOnCatch(&s, &c, 1800);                 // first catch measures the offset outright
  CHECK(s.estKnown == 1 && s.deltaMs == 1800 && s.histN == 0);
  meshSchedOnCatch(&s, &c, 100);                  // then EWMA 0.3
  CHECK(s.deltaMs == 1830 && s.histN == 1 && s.guardMs == 8141);
  for (int i = 0; i < 15; i++) meshSchedOnCatch(&s, &c, (i % 2) ? 400 : -400);
  CHECK(s.histN == 16 && s.guardMs == 1250);      // 2·1.5·400 + 50
  for (int i = 0; i < 16; i++) meshSchedOnCatch(&s, &c, 20);
  CHECK(s.guardMs == 250);                        // clamped to G_min
  CHECK(meshSchedOnMiss(&s, &c) == MESH_SCHED_RETRY && s.guardMs == 500 && s.missRun == 1);
  CHECK(meshSchedOnMiss(&s, &c) == MESH_SCHED_EXTENDED && s.guardMs == 8141);
  CHECK(meshSchedOnMiss(&s, &c) == MESH_SCHED_SWEEP && s.anchored == 0);
  meshSchedReset(&s, &c); meshSchedOnAnchor(&s, &c); meshSchedOnCatch(&s, &c, 0);
  meshSchedOnMiss(&s, &c); meshSchedOnCatch(&s, &c, 10);
  CHECK(s.missRun == 0);

  // ── ladder timing ──
  // early listen before our own window: only the part of G/2 the slot does not cover
  s.guardMs = 1000;  CHECK(meshSchedEarlyMs(&s, 2500) == 0);
  s.guardMs = 8000;  CHECK(meshSchedEarlyMs(&s, 2500) == 1500);
  // next wake = parentOpen + T + delta − slot − early, measured from now
  s.guardMs = 1000; s.deltaMs = 120;
  CHECK(meshSchedSleepMs(&s, 4000, 5000, 900000, 2500, 1000) == 4000u + 900000u + 120u - 2500u - 5000u);
  s.guardMs = 8000;
  CHECK(meshSchedSleepMs(&s, 4000, 5000, 900000, 2500, 1000) == 4000u + 900000u + 120u - 2500u - 1500u - 5000u);
  CHECK(meshSchedSleepMs(&s, 0, 950000, 900000, 2500, 1000) == 1000);   // clamped to the floor
  // in the next wake (t measured from wake): own window opens after `early`,
  // the parent is predicted one slot later, the catch deadline is G/2 after that
  s.guardMs = 8000;
  CHECK(meshSchedOwnOpenMs(&s, 2500) == 1500);
  CHECK(meshSchedPredParentOpenMs(&s, 2500) == 1500 + 2500);
  CHECK(meshSchedCatchDeadlineMs(&s, 2500) == 1500 + 2500 + 4000);

  for (uint32_t r = 0; r < 1000; r += 37) CHECK(meshSchedJitterMs(300, r * 2654435761u) < 300);

  // ── deployment config (mesh_config.h): k = 5/2, pad 50 ──
  MeshSchedCfg d = c;
  d.kNum = MESH_GUARD_K_NUM; d.kDen = MESH_GUARD_K_DEN; d.padMs = MESH_GUARD_PAD_MS;
  d.gMaxMs = meshSchedGuardMax(MESH_SLEEP_INTERVAL_MS, MESH_DRIFT_BIAS_PPM, MESH_DRIFT_STEP_PPM_300S,
                               MESH_GUARD_CAP_MS);
  CHECK(d.gMaxMs == 8140 || d.gMaxMs == 8141);    // T = 900 s production cycle
  meshSchedReset(&s, &d);
  meshSchedOnAnchor(&s, &d);
  for (int i = 0; i < 17; i++) meshSchedOnCatch(&s, &d, (i % 2) ? 400 : -400);
  CHECK(s.histN == 16 && s.guardMs == 2050);      // 2·2.5·400 + 50
  CHECK(meshSchedGuardMax(1800000, MESH_DRIFT_BIAS_PPM, MESH_DRIFT_STEP_PPM_300S,
                          MESH_GUARD_CAP_MS) == MESH_GUARD_CAP_MS);   // 30 min: wander 32.6 s → cap

  if (failures) { printf("%d FAILED\n", failures); return 1; }
  printf("ALL PASS\n");
  return 0;
}
