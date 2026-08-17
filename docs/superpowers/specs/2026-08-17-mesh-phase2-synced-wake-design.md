# Synchronized Wake-Window Deep Sleep (Phase 2) — Design Spec

**Date:** 2026-08-17
**Status:** Designed, not yet implemented — the real-hardware drift-measurement bench
step (see §Drift-measurement bench plan) is the next concrete action, and is a gate
before any firmware work begins.
**Depends on:** `2026-07-26-mesh-deep-sleep-design.md` (Phase 1 — leaf-only sleep,
implemented and field-verified 2026-08-16). This spec is the "Phase 2 (recorded
direction — DO NOT BUILD YET)" section of that document, now designed in full per
`TODO.md §2`'s recorded clock-synchronization discussion.

## Background

Phase 1 (leaf-only sleep) is implemented and field-verified: sleepy nodes deep-sleep on a fixed interval and are structurally barred from being adopted as a parent, with `MESH_FLAG_SLEEPY` beacons rejected before the strict-rank rule in `meshHandleBeacon()`. This means any node with children must stay mains-powered today — exactly the constraint the 2026-08-16 relay test worked around by flipping zone2/3/4 to `sleepy=false`. The goal of Phase 2, as sketched in `TODO.md §2`, is to design (not yet build) a path forward: let every node sleep and still relay, by having wake schedules overlap predictably without long-term clock accuracy — no GPS/NTP anywhere in this system, and no external 32 kHz crystal (ruled out, matches the no-soldering constraint). The core challenge is that a node with a drifted RTC, if left to predict wake instants from accumulated uptime, compounds that drift across the relay chain; a sleepy parent becomes unreachable to its own children, and the system must rediscover parents far more often than the energy budget allows.

The constraints are firm: internal RC oscillator only (the hardware is fixed); adaptive, balanced bias (conservative by default, narrowing per-node once its own drift is known, never pre-committed); and a real bench step to measure per-board drift is mandatory before any constant ships. The deliverable for this design pass is an architecture and design document (matching the existing Phase 1 spec style and structure), not implementation-ready code or on-device validation.

The architecture below was selected via a structured eight-agent design workflow. Four independent proposals addressed the wake-window-overlap problem — nested-TDMA, global-epoch (GSE), bounded-depth-hybrid, and reactive-probe/strobe — alongside a drift-measurement bench plan. Three independent judges then scored the four proposals on battery/reliability fit, solo-thesis implementation risk, and first-principles correctness of the drift-compounding math. **Cascaded Adjacent-Resync TDMA (CART)** won outright on battery/reliability and on drift-compounding correctness; **Bounded-Depth Sleepy Relay** won outright on implementation risk — it reuses more of Phase 1's proven mechanism and adds less new state — while placing a close second on the other two lenses. All three judges independently flagged the same weaknesses in CART and the same fixes borrowed from the other proposals, so the design below is CART's core mechanism plus concrete grafts from the alternatives, not CART unmodified.

## Goals

1. Every node — not just leaves — can deep-sleep at `MESH_SLEEP_INTERVAL_MS` and still relay for its children, collapsing the "sleepy" and "always-on" state machines into one (always-on becomes the degenerate case: guard window ≈ 0, permanently receptive).

2. No global clock, no crystal: every node resyncs against its own immediate parent's live beacon every wake — never predicts an absolute instant, and never trusts a multi-cycle-old estimate.

3. Guard-window cost stays bounded by local fanout, not by rank depth; a node with *N* registered children pays a cost proportional to *N* and the worst single child's uncertainty, not a telescoping sum back to the bridge.

4. Adaptive by default: ships conservative, and each node narrows its own guard window once its own measured drift justifies it; no global hand-tuning, and the narrowing is reversible (widen on miss, try again).

5. A concrete, real-hardware bench step produces the actual drift numbers before any constant is finalized — this design doc deliberately avoids shipping literals, only formulas plus the measurement plan to fill them in.

6. Staged rollout: ship the smallest safe slice first (depth-1 sleepy relay, one sleepy hop maximum), matching this fleet's actual field-observed topology (1–2 hops so far), with a compile-time rollback lever that reproduces exactly Phase 1's proven behavior.

## Non-goals

**Fully general N-hop synchronized relay.** Depth is capped by a tunable constant, shipped conservatively. This fleet's ESP-NOW 7-peer limit (≤ 8 `TRUSTED_NODES` total) makes deep chains rare anyway, and the staged rollout lets field data drive any later loosening rather than betting on paper math.

**Global synchronized epoch (GSE).** Mathematically elegant but the guard-window cost is additive with rank depth — the worked numbers show a cold-start rank-3 node briefly drawing *more* current than staying always-on, a poor fit for the adaptive/balanced bias this design targets, since GSE's dominant fixed cost (the shared window width) doesn't narrow the way a per-node guard pad does. See §Alternatives considered for the full comparison.

**Reactive probe/strobe wake extension.** Sidesteps the drift problem entirely but the entire economic case depends on light-sleep with reliable fast ESP-NOW resume — a capability this codebase has never exercised or validated on real hardware. Recorded as a backlog idea, not part of this design.

**External 32 kHz crystal.** Ruled out — the no-soldering constraint is absolute for this project.

**Implementation.** This pass produces the spec; firmware changes are a separate, later plan once the bench step (below) has run and validated the drift numbers.

## Architecture

### The wake cycle

Every node, every `MESH_SLEEP_INTERVAL_MS`, executes three phases:

**Guard-listen** — wake *G*/2 early (see sizing below), listen up to *G* ms total for the parent's receptive-phase beacon. Waking early by half the guard budget means the true rendezvous instant falls inside the window with symmetric margin, instead of requiring one-sided prediction of whether this cycle runs early or late. This is CART's core: a reactive, catch-triggered resync, not a pre-committed phase commitment.

**Own-work** — send this cycle's reading (flush any RTC-buffered backlog first), using the existing `sendWithConfirm()` / `MESH_TX_CONFIRM_WAIT_MS` mechanism Phase 1 already has. Same reliability semantics — 3 consecutive TX-confirm failures drop the parent.

**Child-receptive phase** — two parts. First, an unconditional knock window (`MESH_KNOCK_WINDOW_MS`, fixed duration, every node, every cycle) — this is what makes new-child discovery possible without paying full relay cost on nodes structurally unable to relay. Second, only if the node has ≥ 1 registered child, an extended service window sized to max(registered children's guard windows) + *child_count* × `MESH_CHILD_EXCHANGE_OVERHEAD_MS` + margin. The max-term dominates and is bounded by the worst single child's uncertainty, not summed — this is what keeps window width *O*(local fanout) instead of growing with depth. Throughout the receptive phase, the node beacons rapidly with a `CHILD_WINDOW_OPEN` flag set only while actually ready for a child unicast — a child just waits until it sees the flag and transmits immediately, which absorbs all of the parent's own-work jitter without the child needing to predict it.

Child registration is free — a direct child is, by definition, whoever's `src_addr` a data unicast arrives from (already how relay-vs-origin is distinguished today) — no separate join packet needed. Each data packet already sent every cycle carries the child's own current guard-window value, which is what a parent uses to size its service window and what a new child's future siblings could borrow as a cold-start prior.

### Guard-window sizing — adaptive, asymmetric, with offset-learning

Per-node state, RTC-persisted alongside the existing `MeshRtcState`:

**AIMD control:** on a successful catch, after *N* consecutive clean hits, narrow *G* by a small fixed decrement, floored at `MESH_WAKE_GUARD_MIN_MS` (radio/CPU wake latency, not drift-related — can't shrink further regardless of clock quality). On a miss, widen multiplicatively (*G* = min(*G*×2, `MESH_WAKE_GUARD_MAX_MS`)) and reset the hit counter.

**Offset-learning:** CART's AIMD narrows the window's width but never re-centers it, so a board with systematic bias — e.g., an oscillator that consistently runs fast — keeps needing a wider-than-necessary symmetric window. Fix: remember where inside the window the parent was actually caught last time, and start next cycle's listen centered near that learned offset instead of the window midpoint; fall back to a full sweep on a miss and re-learn.

**Miss handling:** a single missed catch doesn't mean the parent is lost. A miss just means widen *G*, buffer this cycle's reading, and try the same remembered parent again next cycle — the node never leaves the cheap, normal wake-cycle path for one bad cycle. Escalation to full orphan discovery only happens once the parent is confirmed gone, reusing Phase 1's existing thresholds unchanged: 3 consecutive TX-confirm failures, or no beacon heard for 3× the parent's last-advertised interval. So a node with bad RF luck or scheduling jitter doesn't trigger the expensive full-interval discovery repeatedly; only a sustained, confirmed loss (or having no parent at all — first boot, battery recharged) forces it.

**Cold start:** seed *G* from the fleet-wide conservative default (from the bench step), but prefer a specific parent's own advertised current guard-window value once heard — piggybacking timing stability info on every beacon, not just receptive-phase ones. A parent's demonstrated stability is a better first prior than a global worst case.

`MESH_WAKE_GUARD_MAX_MS` is deliberately not a literal in this document — it's ceil(bench_measured_max_drift_fraction × `MESH_SLEEP_INTERVAL_MS`) + fixed_pad_ms, to be filled in once the bench step produces a real number. `MESH_WAKE_GUARD_MIN_MS` (radio/CPU wake latency) is likewise pending measurement.

### Drift-compounding — the actual argument, not just an assertion

**Across time (successive cycles of the same node):** does not compound. Each cycle's deep-sleep timer is armed fresh off a live radio-verified catch, not off any accumulated prior estimate — a node running six months has the same one-cycle error budget as one that booted an hour ago. This is the load-bearing property of CART: reactive, never predictive.

**Across rank (at a single instant):** bounded, not zero, not unbounded. A child's guard window must cover two single-hop drift terms — its own, plus its immediate parent's (because the parent's own receptive phase is itself only as accurate as the parent's own last catch against *its* parent) — not a telescoping sum back to the bridge. Formally: if parent *P* caught its own parent at observed offset *Δ_P* (real measured drift), then *P*'s receptive phase is centered at that offset ± *P*'s guard width / 2; a child *C* of *P* must cover *C*'s own single-hop drift ± *P*'s window. This is an additive, two-term bound — not exponential — specifically because CART is reactive and catch-triggered (a node's window isn't pre-committed until it actually hears its parent). GSE's rejected additive-with-depth result is real but is a consequence of *that* design's pre-committed-window mechanism, not an inherent property of multi-hop resync in general.

**Load-bearing assumption:** this two-hop bound holds provided every hop catches its parent every cycle. A run of simultaneous misses at multiple hops reintroduces genuine multi-cycle uncertainty that the steady-state argument doesn't cover — mitigated only by AIMD widen-on-miss, not eliminated. This is the main reason for staged depth rollout (next section) rather than shipping full generality immediately. Field data from depth-1 will show whether real boards hit the steady state or encounter repeated miss patterns that compress the window economy below the model.

### Staged depth rollout

All three judges converged on the same concern: CART's depth-independence is a steady-state result not proven under compounding failures, and is a materially larger first build than this fleet's actual topology needs. Adopt a staging discipline as the rollout plan:

**New constant `MESH_SLEEPY_RELAY_DEPTH_MAX`** (ship default: 1) — a sleepy node may only be adopted as a parent if doing so keeps the child's consecutive-sleepy-hop count at or below this cap. An always-on anchor resets the count to zero for its direct sleepy children (it has no receptive-phase uncertainty of its own — it's just always there).

`MESH_SLEEPY_RELAY_DEPTH_MAX` = 0 exactly reproduces Phase 1 — a genuine, built-in regression test and rollback lever, not just a doc claim. If the implementation or field data surfaces a problem, flipping one constant back to 0 is atomic.

**Rollout path:** ship depth-1 (matches the field-observed 1–2 hop topology), run it, gather real per-node reliability and drift field data (Phase 1's own wake logs already do this for free), only then consider raising the cap — never ship full generality on paper-only reasoning. Deploy-time guidance this implies: a physical cluster that would need 2+ consecutive sleepy hops needs an always-on waypoint placed in it, at least until field data justifies raising the cap.

### End-to-end latency for relayed readings

A consequence of the wake cycle's phase ordering — guard-listen → own-work → child-receptive, in that order, every cycle — that the drift-compounding analysis above doesn't cover: a relay sends *its own* reading upstream (own-work) *before* it opens its window to receive from its children (child-receptive). So a child's packet, once it actually arrives during the parent's child-receptive phase, has already missed that cycle's chance to be forwarded upstream — it sits in the parent's buffer (the same RTC-persisted backlog mechanism Phase 1 already uses for a node's own delayed readings) until the parent's *next* wake cycle, a full `MESH_SLEEP_INTERVAL_MS` later, to be relayed further.

This ordering is deliberate, not an oversight: reversing it (service children first, forward upstream last) would risk a node's own regular report missing its own parent's receptive window while it's busy servicing children for that cycle — the design treats a node's own report reliability as the higher priority, at the cost of relay latency.

**The practical cost: a reading from a rank-*R* node takes roughly *R* × `MESH_SLEEP_INTERVAL_MS` to actually reach the bridge — not just its own local read-and-send time.** Each hop adds close to one full sleep interval, not a few hundred milliseconds of radio time. At the shipped default (`MESH_SLEEPY_RELAY_DEPTH_MAX=1`, deepest chain is rank 2), that's up to roughly one extra full interval of staleness beyond the node's own cycle: at 15 min, a depth-2 reading in the app could be up to ~30 minutes old; at 5 min, up to ~10 minutes old. This is a *second*, independent reason to keep the depth cap low — beyond the drift-compounding-failure risk already discussed, every additional hop directly adds a full sleep interval of end-to-end latency, compounding linearly with depth. Worth surfacing on the app side too: the `/mesh` MQTT payload's `ts` field (existing Phase 1 telemetry contract) becomes more meaningful for a relayed depth-2 node than for a leaf, and "recent" should not be assumed to mean "this cycle" once relaying is involved.

### relay_capable opt-out for guaranteed leaves

The unconditional knock window costs every node ~15–20 % more awake time per cycle for zero benefit if that node structurally can never have children (e.g., a known-terminal edge sensor). Add a per-node `TrustedNode.relayCapable` bool (default `true`); a node with `relayCapable=false` skips the knock/service phases entirely and reverts to exactly Phase 1's minimal cycle. Reuses the fleet's existing "compile-time per-node config, reflash to change" model — no new mechanism. A node may be both `sleepy=true` and `relayCapable=false` (leaf on battery), or `sleepy=false` and `relayCapable=false` (mains-powered leaf, saves energy for other uses).

### First-boot and orphan discovery

Every candidate parent runs its fixed knock window unconditionally, every cycle — this is load-bearing. Because every node shares the same global cycle period, an orphan that listens continuously for one full `MESH_SLEEP_INTERVAL_MS` is mathematically guaranteed to sweep through at least one occurrence of every candidate's knock window, regardless of that candidate's unknown phase offset. Worst case ≈ one full sleep interval of continuous RX (≈ 15 min at production cadence) — a real, stated cost increase over Phase 1's cheap 5 s discovery, but bounded and rare (first boot, occasional re-parent), not unbounded. Piggybacking timing hints on every beacon shrinks the practical case well below this worst-case bound: a listening orphan learns each candidate's offset within a few cycles and can predict the next knock window, collapsing the tail of the wait from minutes to seconds.

**Named case — battery ran flat, was recharged, redeployed:** Not a new mechanism — this is the first-boot path above, unchanged. RTC memory (parent MAC, guard window, seq counters, buffered readings) only survives deep sleep because the RTC power domain stays energized off the battery at low current; if the battery actually depletes to zero, that power is gone too, so the state is lost exactly as it would be on a never-flashed board. The existing magic-word + `esp_sleep_get_wakeup_cause()` guard (Phase 1, unchanged) can't tell the difference and correctly falls back to cold-boot discovery. The board still knows who it is (`TRUSTED_NODES[]` is keyed by MAC, burned into silicon, unaffected by power loss) — it just has no memory of where in the mesh it used to sit, so it re-adopts a parent via the guaranteed-sweep discovery, and its guard window re-earns tightness from the cold-start default over the next several cycles. Sequence-number reset to 0 isn't a de-dup hazard either: the bridge's per-origin de-dup cache is time-bounded well under `MESH_SLEEP_INTERVAL_MS` (`MESH_DEDUP_WINDOW_MS`), so it ages out long before a realistic recharge finishes. No Pi-side or bridge-side state needs resetting or re-registering.

### Marginal-RF-link handling

All three judges independently flagged the same concrete risk: a node on a marginal RF link (this fleet has already benched and pulled one — zone1/A1:B0, bad TX antenna) is exactly the node most likely to need re-parenting's expensive full-interval discovery repeatedly, which could burn more battery than just leaving it always-on — the one scenario where Phase 2 is net-worse than Phase 1 for that specific node. **Recommendation:** RSSI-hysteresis reluctance to drop a marginal-but-still-alive parent (avoid flapping); a node with observed link marginality should default to `relayCapable=false` and/or stay `sleepy=false` (mains) until its RF quality is fixed. This is a configuration and deployment-time decision, not a firmware fallback — the adaptive window narrows against a parent it's catching regularly, so it can't know a link is marginal until the parent vanishes; keeping a weak link's node off the sleepy-relay path is a deployer decision, not something the algorithm detects on its own.

### Phase 1 invariant replacement

Phase 1's invariant: "a beacon flagged sleepy is never a parent candidate" — structural, static, checked before the strict-rank rule. Phase 2 removes that hard exclusion; **strict-rank** (parent's rank strictly below child's) remains completely untouched as the sole loop-safety mechanism — nothing here touches routing safety. What replaces the removed reachability guarantee: a sleepy beacon is a valid candidate if and only if its `sleepy_chain_depth ≤ MESH_SLEEPY_RELAY_DEPTH_MAX` and it's currently advertising an active receptive window; tie-break prefers the candidate advertising the smaller current guard window when rank/RSSI tie, since the chosen parent now directly determines the child's own future guard-window cost.

## Wire format changes (one more fleet-wide reflash)

Grown at the **end** of each struct per the existing convention — existing field offsets remain unchanged. Length continues to disambiguate message type (previously 19 vs 33 bytes; now 21 vs 34 bytes).

```c
// MeshBeacon — 19 → 21 bytes
typedef struct __attribute__((packed)) {
  uint8_t  magic;
  uint8_t  mac[6];
  uint8_t  rank;
  uint16_t seq;
  uint32_t beacon_interval_ms;
  uint32_t window_duration_ms;      // repurposed: sender's own receptive-phase width
                                    // this cycle (was "bridge-originated, unused")
  uint8_t  flags;                   // bit0: MESH_FLAG_SLEEPY (now telemetry-only)
                                    // bit1: NEW MESH_FLAG_CHILD_WINDOW_OPEN
                                    //       (set only while receptive)
  uint8_t  own_guard_window_q_sec;  // NEW — quarter-second units; cold-start
                                    // prior for children
  uint8_t  registered_child_count;  // NEW — informational/diagnostic + topology
} MeshBeacon;

// MeshDataPacket — 33 → 34 bytes
typedef struct __attribute__((packed)) {
  uint8_t      magic;
  uint8_t      origin_mac[6];
  uint8_t      origin_rank;
  uint8_t      ttl;
  uint16_t     seq;
  SensorPacket payload;
  uint16_t     battery_mv;
  uint8_t      parent_mac[6];
  int8_t       parent_rssi;
  uint8_t      flags;
  uint8_t      own_guard_window_q_sec;  // NEW — child reports its own current
                                        // guard window; doubles as parent's
                                        // child-registration/refresh signal
} MeshDataPacket;
```

These additions are purely appended; the wire-format invariant established in Phase 1 persists: length uniquely identifies message type, eliminating runtime type ambiguity regardless of protocol evolution.

## Config additions (`mesh_config.h`)

All timing constants are expressed as formulas or bench-pending placeholders; no numeric literal commits until the drift-measurement bench (§Drift-measurement bench plan) produces real hardware data.

```c
#define MESH_SLEEPY_RELAY_DEPTH_MAX         1
                                            // ship default; 0 = exactly Phase 1

#define MESH_KNOCK_WINDOW_MS                /* ~500 ms, bench-pending */

#define MESH_CHILD_EXCHANGE_OVERHEAD_MS     /* ~150 ms/child, bench-pending */

#define MESH_WAKE_GUARD_MIN_MS              /* ~250 ms — radio/CPU floor,
                                               not drift-derived */

#define MESH_WAKE_GUARD_MAX_MS              /* ceil(bench_max_drift_fraction
                                               × MESH_SLEEP_INTERVAL_MS) + pad */

#define MESH_GUARD_NARROW_STEP_MS           /* small; after N consecutive
                                               clean hits */

#define MESH_CHILD_REGISTRATION_TTL_CYCLES  /* ~3 cycles of silence before
                                               evicting */

struct TrustedNode {
  uint8_t     mac[6];
  const char* zone;
  bool        sleepy;            // existing
  bool        relayCapable;      // NEW — default true; false = Phase-1
                                 // identical minimal cycle
};
```

None of the numeric placeholders above should be committed as concrete values until the bench step produces actual measurements.

## Drift-measurement bench plan (mandatory prerequisite)

Reuses the existing telemetry path unmodified — fake-sensor firmware's sleepy wake cycle feeds through the bridge to serial_bridge.py to MQTT — plus one small bench-only tap. Mains-powered throughout (no soldering required); boards remain at their benches.

**Boards:** Zone2 (9D:B0), zone3 (6B:50), zone4 (75:EC) — run all three independently because RC drift is per-die, not extrapolatable from a single unit. Zone1 (A1:B0) is excluded due to its dead TX antenna; re-bench once the antenna is repaired.

**Setup:** Reflash the fleet with zone2/3/4's sleepy flag flipped back to true. This completes the revert flagged in the 2026-08-16 relay test, preparing the boards for the bench run.

**Phase A — Shakeout (~30 min per board):** Run 30 cycles at the bench 60-second interval, monitoring the serial console live for genuine timer wakes and first-attempt delivery success before committing to an unattended multi-hour run.

**Phase B — Main measurement (~24 h, all 3 boards concurrently):** Continuous cycling at 60-second intervals (~1440 cycles per board), spanning a full day/night thermal cycle to capture the dominant driver of RC-oscillator variation. Yields the statistical distribution shape (jitter distribution, convergence rate).

**Phase C — Interval-scaling check (~12 h each at both candidate production rates):** Run both 900-second (15-minute) and 300-second (5-minute) intervals — approximately 48 cycles per board at 900 s and ~144 cycles at 300 s. The production interval is not yet finalized (both rates remain under evaluation for the power/data-freshness tradeoff — see §Open risks / explicitly deferred), so benching both now avoids a second bench run once the interval is decided. Though ppm should theoretically be interval-independent, the bench provides cheap insurance before extrapolating a design constant from early measurements.

**Method:** A small bench-only Python script on the Pi subscribes to `greenhouse/nodes/+/mesh` (QoS 1, filtering out retained messages), logging `(mac, monotonic_arrival_time, payload)` per line with immediate flush. Fit `t_i ≈ t_0 + n_i·T_actual` (ordinary least-squares or Theil-Sen estimator) per board; compute `ppm = (T_actual − T_nom)/T_nom × 1e6` and report with a confidence interval, not a bare point estimate. Exclude any gap that isn't a clean single-cycle sample (missed wake, discovery retry, or extraneous relay hop) before fitting.

**Feeds the design:** Worst observed |ppm| across all three boards (plus a conservatism margin, since board placement is fixed in the field) scales the `MESH_WAKE_GUARD_MAX_MS` formula's drift-response term; the residual jitter standard deviation sizes its fixed floor term; the spread between the three boards' ppm values themselves empirically justify the case for per-node adaptive sizing (if they cluster tightly, a single global constant would be nearly as effective).

## Testing / rollout checklist

1. Run the bench-drift step to completion with constants filled in from real numbers.
2. Build with `MESH_SLEEPY_RELAY_DEPTH_MAX=0` first to confirm byte-for-byte behavioral parity with Phase 1 (regression check).
3. Build with `=1` and test on an isolated bench pair: one always-on-adjacent sleepy relay and one sleepy leaf child, before touching the rest of the fleet (the relay-forwarding-in-a-tight-timebox piece is flagged by every proposal as the fiddliest new firmware work).
4. Deploy full fleet at depth-1 and verify knock-window discovery, service-window sizing, guard narrowing over at least 20 real cycles, and offline-detection timing. Also measure actual end-to-end latency for a depth-2 relayed reading (sensor-read timestamp to bridge-arrival timestamp) against the ~1×`MESH_SLEEP_INTERVAL_MS` prediction in §End-to-end latency for relayed readings.
5. Deliberately kill a parent mid-sleep and confirm re-parenting discovery, RSSI-hysteresis behavior, and battery cost on marginal links (if one exists).
6. Only after sustained depth-1 field time: evaluate whether raising `MESH_SLEEPY_RELAY_DEPTH_MAX` is justified.

## Alternatives considered

This design (CART: Cascaded Adjacent-Resync TDMA, plus grafts from rejected proposals) was chosen from four independently-generated architecture proposals scored by three judges on battery/reliability fit, solo-thesis implementation risk, and drift-compounding correctness.

### Global Synchronized Epoch (GSE)

Proposed having every node target the same absolute, network-wide wake instant (propagated via an epoch counter on every beacon) instead of resyncing relative to just its immediate parent. Mathematically the most elegant of the four — a whole relay chain threads through one shared window instead of nested per-parent windows, and re-parenting doesn't require re-learning a schedule since any surviving neighbor shares the same global epoch. Rejected because its guard-window cost is additive with rank depth (unlike the chosen design's depth-independent bound), and its own worked numbers showed a cold-start rank-3 node briefly drawing more current than just staying always-on — a poor fit for the adaptive/balanced bias this design was built around, since the dominant fixed cost (the shared window width) doesn't narrow with per-node calibration the way the guard pad does. Its useful ideas were still adopted: piggybacking timing/stability hints on every beacon (not just receptive-phase ones), and preferring the more-stable candidate as a parent-selection tie-break.

### Reactive Probe/Strobe Wake Extension

Proposed sidestepping drift math entirely — a child unicasts a burst of short "wake probe" packets until it gets an ACK from its parent, rather than predicting when the parent will be awake. This trades compounding uncertainty for bounded latency and is robust to any drift magnitude by construction. Rejected as the primary architecture because its entire economic viability depends on ESP32-C3 light-sleep with reliable fast ESP-NOW receive resume — a capability this codebase has never exercised or validated; if that capability doesn't work as hoped, the design's relay-node battery cost could plausibly end up worse than Phase 1's always-on-relay model, by the proposal's own admission. Its offset-learning idea (remembering where in a window contact last succeeded, and starting there next cycle instead of a full sweep) was still adopted into the chosen design's guard-window algorithm. Light-sleep viability is kept as a backlog spike to investigate independently, not a dependency of this design.

## Open risks / explicitly deferred

Recorded for the write-up, not building now.

- **Correlated sibling drift:** No mechanism yet for same-rank neighbors in a shared thermal enclosure drifting similarly rather than independently. Low-probability at this fleet's scale; worth one line in the write-up, not a dedicated mitigation.

- **N-hop generality beyond the depth cap:** Explicitly out of scope until field data from the depth-1 rollout justifies it.

- **App-controlled runtime cycle/config changes (no reflash):** Today there is no downlink at all (sensors only beacon/send upward; nothing sends commands back down), and `MESH_SLEEP_INTERVAL_MS`/`TRUSTED_NODES[]` are compile-time, matching Phase 1's existing "reflash the small fixed fleet" model — already a named Phase 1 non-goal ("no OTA/remote toggling"), kept unchanged here too. Theoretically buildable later: this design's child-receptive window already gives the parent a natural bidirectional moment to piggyback a config push on, but making the interval dynamic would ripple into the guard-window math, the bridge's per-node offline thresholds, and the drift-compounding bound (all of which currently assume one shared, known-in-advance interval), plus needs safe handling of a config push that only reaches part of the mesh. Parked, not designed.

- **Candidate production interval: 5 min vs 15 min, not yet decided:** From this project's power-budget analysis (mAh/day ≈ 5190/T + 1.5, T in seconds), assuming the optimized-hardware numbers (LED desoldered, LDO bypassed): 15 min (900s) → 7.3 mAh/day, ~60× solar margin (worst-case Athens winter), ~165 days battery-alone reserve; 5 min (300s) → 18.8 mAh/day, ~23× solar margin, ~64 days battery-alone reserve. Both are comfortably solar-sustainable — even 1 min (60s) stays net-positive (~5× margin) — so the real tradeoff isn't "does it survive," it's how much reserve there is for a multi-week dark stretch, traded against how fresh the data is. Important caveat, not yet resolved: those numbers assume the hardware mods (LED removal, LDO bypass) are actually done on the boards; the power-budget doc itself says they're not yet done and the sleep-current figure is unvalidated on real hardware — given the no-soldering constraint on this project, real consumption could be materially worse. Decision deferred until real numbers exist: the drift-measurement bench plan benches both 300s and 900s, so whichever interval is picked, the drift data needed to size the guard window is already on hand.

## Recommended implementation sequencing

This is the recommended order for after this design pass — not built as part of this pass.

1. Phase 1 is already done. Leaf-only sleep is implemented and field-verified (2026-08-16 relay test). Nothing to redo.
2. Next: the drift-measurement bench step, with zero firmware changes. Reflash zone2/3/4 back to `sleepy=true` (already the flagged "revert before deploy" item from the relay test) — that's a config revert, not new code. Add the one small bench-only Python tap on the Pi. Run Phases A/B/C. This step alone answers the biggest open question: whether `MESH_WAKE_GUARD_MAX_MS` lands in the tens-of-ms or multi-second range, which determines whether Phase 2's numbers even look attractive before writing any firmware for it.
3. Only then: implement Phase 2, in the staged order from the Testing/rollout checklist above — `MESH_SLEEPY_RELAY_DEPTH_MAX=0` regression build first, then an isolated depth-1 pair, then the full fleet, informed by real numbers instead of the placeholder formulas in this doc.

This ordering isn't just cautious sequencing for its own sake — Phase 1's own spec explicitly said Phase 2 shouldn't be designed in numeric detail until this data exists, and this design has several places (e.g. whether the adaptive/depth-independent bound actually holds, whether a relay node's battery cost stays meaningfully better than staying always-on) that are genuinely unverifiable on paper. Worth treating the bench step as a real go/no-go gate: if worst-case drift comes back much worse than the codebase's other timing constants (`MESH_WAKE_DISCOVERY_MS`=5s, `MESH_TX_CONFIRM_WAIT_MS`=500ms) suggest, that's a legitimate reason to reconsider scope (e.g. ship only `MESH_SLEEPY_RELAY_DEPTH_MAX=1` for hand-picked good-RF-link nodes) rather than push ahead on optimistic assumptions.
