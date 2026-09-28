# CART depth N — every node sleeps and relays (ladder schedule)

**Date:** 2026-09-28
**Status:** Implemented on branch `feat/cart-depth-n`, compile-verified, **not yet flashed or bench-tested**.
Built on user instruction "do the firmware with the data we have and best guesses" — the Gate 0
drift bench (run 2) is still pending, so every drift-derived constant below is provisional.
**Supersedes for depth ≥ 2:** the depth-cap-1 design of `2026-09-23-cart-v2-revision.md` §3.2.
Its guard policy (§3.3), jitter/attempts (§3.5), TTL (§3.6) and misconfiguration catalogue (§4) still apply.

## Deployment constraint

No always-on relays. Every field node is battery + solar, deep-sleeps, and relays for others.
Only the Pi and the bridge next to it are mains powered (always listening, rank 0).

## Mechanism: the ladder

Each node keeps its own receive window (for its children) immediately **before** its parent's
receive window:

```
        rank 3 window   rank 2 window   rank 1 window   → bridge (always on)
time ─► [ r3 open ....][ r2 open ....][ r1 open ....] tx
             ▲ r4 sends     ▲ r3 sends     ▲ r2 sends    ▲ r1 sends
```

- A node **opens** its window (RX_OPEN beacons every 100 ms) at `predicted parent open − SLOT`.
  Its children catch those beacons and send during `[open, open + SLOT]`.
- The node then **catches its own parent's RX_OPEN beacon** at the end of its window. That catch
  re-anchors its clock to the parent, and it forwards everything it holds (own readings + relayed
  frames) inside the parent's window.
- A node whose parent is always-on (rank 1 under the bridge, or any mains node) runs free: window
  of `SLOT`, then sends straight away. Its period `T` is its own RTC's.
- Clock error is **pairwise only**: each node anchors to its own parent every cycle, so nothing
  accumulates along the path (meshsim: a common T2 window would need ~2·3·σ·√depth of extra
  width — 16 s at depth 50 — while the ladder needs one guard per node).
- End-to-end latency ≈ depth × SLOT inside one cycle (50 × 2.5 s = 125 s at depth 50).

## Why T1 (hop-by-hop custody), not T2

meshsim at 50 × 10: T2 needs every node on a path awake at once, the window grows with depth and
exceeds the 10 s awake backstop, and synchronized bursts collide heavily. The ladder keeps each
node awake ≈ `boot + early + SLOT + G/2 + tx`, independent of depth.

Custody = the ESP-NOW L2 ACK (`ESP_NOW_SEND_SUCCESS`) from the parent. A frame is removed from a
buffer only after its parent's radio acknowledged it; otherwise it stays in RTC memory for the
next cycle. The Pi's app-level ACK cannot reach a node that is already asleep, so CART nodes no
longer wait for it (the Pi keeps sending it; always-on / Phase-1 nodes keep using it).

## Constants (best guesses — all in `mesh_config.h`)

| Constant | Value | Basis |
|---|---|---|
| `MESH_CART_ENABLE` | 1 | 0 = exact Phase 1 behaviour (rollback) |
| `MESH_CART_SLOT_MS` | 2500 | ≥ DHT22 warm-up 2000 ms + read, so the own reading is ready when the node sends; sensors warm up *inside* the window |
| `MESH_CART_JITTER_MS` | 300 | meshsim: 100 ms gave 47–62 % collisions per attempt with 10 nodes/rank; 1 s gave 14 % |
| `MESH_CART_ATTEMPTS` | 3 | spec §3.5 |
| `MESH_RX_BEACON_PERIOD_MS` | 100 | spec §3.5 |
| `MESH_WAKE_GUARD_MIN_MS` | 250 | spec §3.3 |
| `MESH_DRIFT_BIAS_PPM` | 1700 | Gate 0 run 1, worst pair, robust stats |
| `MESH_DRIFT_STEP_PPM_300S` | 100 | planning value (run 2 pending) |
| `G_max` | `max(2·b·T·1.3, 6·σ_wander)`, cap 20 s | spec §3.3 bias rule **plus** the temperature-wander term meshsim showed the bias rule misses |
| `MESH_RELAY_BUFFER_SIZE` | 50 frames | RTC FAST 8 KB budget (3050 B); rank-1 subtree at 50 × 10 |
| `MESH_MAX_TTL` / Pi `ACK_TTL_MAX` | 64 | the 16 cap stopped delivery beyond rank 17 |

## Parent selection

A sleepy beacon is adoptable only when it carries `RX_OPEN | RELAY_CAP` (the sender is routed,
relay-capable and currently receiving). Strict-rank and RSSI rules are unchanged. An always-on
parent of equal or better rank is preferred to a sleepy one. Adopting a sleepy parent from inside
its window is itself a catch (anchor). Admission control: a relay whose buffer is full adds
`BUF_FULL` to its beacons; children then keep their frames for the next cycle.

## Failure handling

- Missed parent window: guard doubles (retry), then `G_max` (extended), then after 3 misses the
  parent is dropped and the next wake is an **orphan sweep**: listen up to `T + SLOT` for any
  adoptable beacon (bridge / always-on / sleepy with RX_OPEN). Everything stays buffered in RTC.
- Relay buffer full: incoming frames are dropped and counted (`BUF_FULL` should prevent it).
- 3 consecutive L2 failures do not drop a sleepy parent inside a CART cycle (collisions are
  expected there); only missed windows do.

## Not in this change

Re-parenting to a better sibling mid-life (beyond strict rank on beacons heard while awake),
guard hints in beacons, per-child service windows. The sim (`sim/meshsim`, branch
`feat/mesh-simulator`) models the ladder as `T1-ladder`.

## Validation still required on hardware

1. Compile ✓ (this change). 2. Two-node pair with `MESH_TEST_IGNORE_BRIDGE`: catch rate, awake
time, no lost readings. 3. Three bench nodes in a chain. 4. Gate 0 run 2 → replace the drift
constants.
