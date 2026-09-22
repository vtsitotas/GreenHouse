# CART Phase 2 — v2 Revision (research-validated) — Design Spec

**Date:** 2026-09-23
**Status:** Designed, not implemented. Supersedes the *mechanism details* of
`2026-08-17-mesh-phase2-synced-wake-design.md` (CART) wherever the two differ;
that document's background, goals and rejected alternatives (GSE, strobe) still
stand. Implementation plan: `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md`.
Reproducible numbers: `docs/analysis/cart_sim.py` → `docs/analysis/cart_sim_results.txt`.

## 1. Why a revision

The 2026-08-17 CART spec was written before the v2 mesh ("unlimited sensors",
AES-GCM + CMAC, `nodes.json` trust store) shipped, and before the
application-layer ACK (`2026-09-16-mesh-app-ack-design.md`). Re-checking it
against today's code and against real drift data found four problems, three
of which would make it fail silently if built as written:

| # | Problem in the 2026-08-17 spec | Consequence if built as written |
|---|---|---|
| 1 | The child reports its guard window **inside the data packet body** (`own_guard_window_q_sec` appended to `MeshDataPacket`). In v2 the body is AES-GCM ciphertext that only the Pi can open. | The relay can never read it → cannot size its service window. |
| 2 | Wire formats are the v1 structs (19-byte beacon, 33-byte plaintext data packet, `TrustedNode.relayCapable` in `mesh_config.h`). | None of it applies to v2 (27-byte authenticated beacon, 61-byte sealed packet, runtime enrolment). |
| 3 | Guard sizing is AIMD with no rule linking the ceiling `MESH_WAKE_GUARD_MAX_MS` to clock *bias*. | Simulation (§4.2): with a realistic 0.6 % bias at T = 900 s and an 8 s ceiling, **90–100 % of wakes miss** and the node falls into a permanent orphan-sweep loop (~690 mAh/day). |
| 4 | Phase order is always *own-work → child-receptive*, adding one full interval of latency per sleepy hop. | At the shipping cap (one sleepy hop) this is unnecessary (§3.2), and it makes the application ACK structurally impossible for relayed leaves (the ACK comes back after both nodes are asleep). |

## 2. Research findings that drive the numbers

- **RC slow clock is the dominant error source.** The ESP32-C3's default
  RTC_SLOW_CLK is the internal ~136 kHz RC oscillator — lowest deep-sleep
  current, but its "frequency stability … is affected by temperature
  fluctuations" (ESP-IDF, System Time). Field reports on ESP32-class chips
  using it: ~23 s/hour drift (0.64 %), ~10 min over 9 h (1.9 %), ~8 min/day
  (0.56 %) even at stable temperature, and up to ~10 % relative error before
  calibration. The model below uses **0.6 % relative bias** as the planning
  value; Phase 0 replaces it with measurement.
- **A better source exists at +5 µA.** The internal ~17.5 MHz oscillator ÷256
  "provides better frequency stability than the internal RC oscillator at the
  expense of a higher (by 5 µA) Deep-sleep current" (ESP-IDF). 5 µA × 24 h =
  0.12 mAh/day — under 2 % of a leaf's 7.26 mAh/day budget. An external 32 kHz
  crystal is better still but is ruled out by the no-soldering constraint.
  Phase 0 measures both internal sources.
- **What matters is the *relative* error between parent and child.** Nodes
  in the same greenhouse see correlated temperature swings, so absolute drift
  numbers overstate the problem; bias (constant per pair) is learnable;
  only the *change* in relative rate from one cycle to the next must be
  covered by the guard window. The Phase 0 bench therefore reports pairwise
  relative drift, not only per-board ppm.
- **ESP-NOW transmits at 1 Mbps by default** (Espressif ESP-FAQ). Airtime:
  27-byte beacon ≈ 0.63 ms, 61-byte data frame + L2 ACK ≈ 1.2 ms. All
  collision numbers below use these worst-case (slowest-rate) values.

## 3. Architecture changes

### 3.1 v2 alignment

- **Guard hint travels in the child's beacon, not its data packet.** A
  sleepy child already emits one beacon per wake. Under CART that beacon
  (cleartext + NetKey nettag, readable by any member) carries
  `guard_hint_q` = the child's current guard window in 250 ms units. The
  relay registers the child from this beacon (MAC + guard hint) and from the
  data frame's `src_addr`. **The 61-byte sealed data packet does not change**
  — the Pi's data path is untouched.
- **Beacon v3 — 29 bytes** (unique among the message lengths 8 / 20 / 29 /
  33 / 61). Appended *before* the nettag so the nettag covers them:
  `sleepy_depth` (uint8, consecutive sleepy hops from the nearest always-on
  anchor, 0 for always-on nodes) and `guard_hint_q` (uint8). New flag bits:
  bit1 `MESH_FLAG_RX_OPEN` (relay's receptive window is open right now),
  bit2 `MESH_FLAG_RELAY_CAP` (sender is willing to relay). Existing fields are
  reused: `beacon_interval_ms` = the sender's cycle period T when sleepy,
  `window_duration_ms` = milliseconds left in the open receptive window.
  Magic stays `MESH_MAGIC_V2`; length is the discriminator (project rule).
- **`relayCapable` comes from provisioning, not a compile-time struct.** The
  provisioning blob's flags byte (`plain[16]`) becomes a bit field: bit0
  sleepy, bit1 *leaf-only* (not relay-capable). **Hazard:** today's firmware
  tests `plain[16] == 1`; a blob with any extra bit set would make an old
  node read *not sleepy* and run always-on, draining its battery in hours.
  Guard: CART firmware announces itself with a new join-beacon marker
  (`0x4B`, vs today's `0x4A`); the bridge forwards `"caps":1`; the Pi only
  ever sets bit1 for a MAC whose last join beacon carried caps ≥ 1.
  Absent NVS key ⇒ relay-capable (so an upgraded, already-enrolled fleet
  gets relaying without re-enrolment); `MESH_SLEEPY_RELAY_DEPTH_MAX = 0`
  remains the fleet-wide rollback lever.

### 3.2 Shipping cap = one sleepy hop ⇒ receive-then-forward

With `MESH_SLEEPY_RELAY_DEPTH_MAX = 1`, a sleepy relay's own parent is by
definition always-on (bridge or mains node). Consequences:

1. **The relay needs no guard window.** Its parent is always receptive; it
   unicasts on wake using its cached parent MAC (exactly Phase 1). The relay
   is the *time anchor* of its sleepy children; only children carry guard
   windows.
2. **Cut-through forwarding.** The 2026-08-17 order (*own-work →
   child-receptive*) existed so a relay's own report could not miss a
   *sleepy* parent's window. With an always-on parent that risk does not
   exist, so the relay forwards each child frame upstream the moment it
   arrives. Added latency for a relayed reading: **0 intervals** (was 1).
3. **ACK works in-cycle.** The Pi's accept/reject for the child's frame
   comes back through the bridge in one UART round trip (Pi-side decrypt +
   two JSON lines at 115200 baud: estimated 50–300 ms, measured in Phase 0);
   the awake relay re-floods it; the child is still inside its
   `MESH_APP_ACK_WAIT_MS = 2000` wait. `meshHandleAck()`'s relay gate changes
   from `!sleepy` to `!sleepy || rxWindowOpen`.
4. If the cap is ever raised to ≥ 2, relays whose parent is sleepy fall back
   to the original *own-work first* order — selected per cycle from the
   parent's `sleepy_depth`, not by a global switch.

Relay cycle: wake → open receptive window (beacon with `RX_OPEN` every
100 ms) for `S` → forward own reading + each child frame as it arrives →
re-flood matching ACKs → close window → sleep.
Leaf cycle: wake `G/2` before the predicted window start → listen for an
`RX_OPEN` beacon from the parent → wait a jitter slot → send → wait for L2
ACK and app ACK → re-arm the RTC timer from the *observed* catch time →
sleep.

### 3.3 Guard window: dispersion-based policy, with a bias-derived ceiling

Replace AIMD with a **margin** policy:
`G = clamp(2 · 1.5 · max|err| over the last 16 catches + 50 ms, G_min, G_max)`,
doubling on a miss. It narrows only as far as the observed dispersion
justifies instead of ratcheting down until misses occur.

| T = 900 s, bias 0.6 %, G_max = 14 s | AIMD (2026-08-17) | margin (this spec) |
|---|---|---|
| step 0.01 %: miss rate | 1.43 % | **0.93 %** |
| step 0.01 %: mean listen | 1.34 s | **0.55 s** |
| step 0.03 %: drops (3 misses in a row) per year | 169 | **129** |

(`step` = per-cycle innovation of the temperature-driven relative rate; see
`cart_sim.py` §A2. The margin policy is better in every simulated row.)

**Ceiling rule (new, load-bearing):** after any re-anchor (first boot,
sweep), the per-cycle offset is unknown until the *next* catch measures it,
and that next window is centred on zero. It can only succeed if
`G_max ≥ 2 · |b| · T` (b = relative bias). Planning value:
`G_max = 2 · |b| · T · 1.3`. With b = 0.6 %: **4.7 s at T = 300 s, 14.0 s at
T = 900 s.** Below that line the system cannot bootstrap: the 8 s fixed
ceiling at T = 900 s gives 90–100 % misses in simulation.

`G_min = 250 ms` (radio/boot jitter floor). Wake latency (≈ 140–230 ms,
report §5.6) is a constant and is absorbed into the learned offset.

### 3.4 Cycle length: 300 s recommended for relay-enabled clusters

Temperature change per cycle scales with cycle length, so the fair
comparison is T = 300 s at step s against T = 900 s at step 3s:

| | T = 300 s (step 0.01 %) | T = 900 s (step 0.03 %) |
|---|---|---|
| Miss rate (margin policy) | **0.18 %** | 2.00 % |
| Orphan sweeps per year | **5** | 129 |
| Leaf energy: base + listen + sweeps + ACK wait | 18.79 + 1.21 + 0.10 + 2.08 = **22.2 mAh/day** | 7.26 + 3.58 + 7.66 + 0.69 = **19.2 mAh/day** |
| Battery-only autonomy (1200 mAh usable) | 54 days | 62 days |
| Solar margin (437 mAh/day worst winter, report §18.2.3) | ×19.7 | ×22.8 |
| Relay worst-case awake, 6 children, one at G_max (§3.7 formula) | 6.9 s — fits the 10 s cap | 11.5 s — **exceeds the 10 s cap** |
| Data freshness | 5 min | 15 min |

T = 300 s costs ~16 % more energy but is an order of magnitude more robust
and fits the existing awake backstop. Decision rule (Gate 0, §6): pick the
largest T whose simulated miss rate ≤ 1 % and sweeps ≤ 12/node/year with the
*measured* drift; T = 300 s is the planning default. This settles the
"5 vs 15 minutes" question the 2026-08-17 spec left open.

### 3.5 Collisions inside synchronised windows

CART concentrates traffic: every child of a relay wakes into the same window
and is released by the same `RX_OPEN` beacon. Two effects:

- **In-range children** contend through 802.11 CSMA/CA; the first backoff
  slot collides with probability 6.25 % (k = 2), 12.1 % (k = 4), 17.8 %
  (k = 6) — resolved by MAC retry, costing only milliseconds.
- **Hidden children** (out of range of each other, both in range of the
  relay) collide at the relay like pure ALOHA. With each child waiting a
  uniform jitter in [0, J) after the trigger and retrying up to 3 times:

| k children, all hidden | J = 20 ms, 1 try | J = 100 ms, 1 try | **J = 100 ms, 3 tries** |
|---|---|---|---|
| 2 | 11.4 % | 2.4 % | **0.003 %** |
| 4 | 30.7 % | 6.9 % | **0.04 %** |
| 6 | 45.9 % | 11.3 % | **0.15 %** |

Design: **J = 100 ms, 3 attempts, at most 6 children per sleepy relay**
(admission control: a full relay stops setting `RELAY_CAP` in its beacons;
this also bounds its RTC buffer). The service window therefore needs
`S = G_child/2 + 3·J + n·150 ms`. `RX_OPEN` beacons every 100 ms add
0.6 % channel occupancy — negligible.

### 3.6 TTL

- **Data: TTL assigned at transmit time, not seal time.** TTL is outside
  both the GCM AAD and the nettag (header byte 15), so the origin may
  rewrite it on every (re)transmission. Today a reading sealed at rank r
  gets TTL r + 2 and keeps it in the buffer; if the node later re-parents
  deeper than r + 2 the buffered reading expires on the way. Rule:
  `ttl = current_rank + MESH_TTL_MARGIN` at each transmission,
  `MESH_MAX_TTL = 16` while unrouted.
- **ACK: TTL = target rank + 2, set by the Pi** from the frame's cleartext
  `header.rank`, instead of the fixed `MESH_ACK_TTL = 6`. With dedup every
  node re-floods at most once, so TTL does not change the *per-node* cost;
  it stops the flood from reaching nodes deeper than the target, which is
  where it is wasted. A rank-k node receives the ACK with
  `ttl = TTL₀ − (k − 1)` and re-floods only if that is > 0, so reaching a
  target at rank r needs `TTL₀ ≥ r − 1`; `r + 2` leaves a 3-hop margin for a
  re-parent between the uplink and the ACK.

### 3.7 Discovery, awake budgets, parent loss

- **Orphan → sleepy relay** can only happen during that relay's window, once
  per T. Guaranteed discovery needs one full-cycle sweep: 7.2 mAh at 300 s,
  21.6 mAh at 900 s, one-off. Allowed only when battery > 30 %; otherwise a
  phase-scanning sweep of 60 s per cycle at rotating offsets
  (≤ T/60 cycles).
- **Orphan → always-on node:** fixes the §21.2 report finding. An always-on,
  routed node that hears an `UNROUTED` (rank 255) beacon from a MAC it has
  not heard in the last 60 s calls `meshTrickleReset()` (rate-limited to
  once per 10 s), so the orphan's 5 s discovery window meets a 2 s beacon
  instead of a 60 s one (8.3 % → ~100 % catch per wake).
- **Parent loss:** 3 consecutive misses → one extended listen at `G_max` →
  only then a sweep. Advertised `beacon_interval_ms` of a sleepy parent is T,
  so the beacon-timeout detector naturally becomes 3T.
- **Awake backstop per role:** leaf `MESH_WAKE_MAX_AWAKE_MS` stays 10 s;
  relay backstop = `2.5 + 0.5 + G_max/2 + 3·J + 6·0.15 + 0.3 s` = 6.85 s at
  T = 300 s (11.5 s at T = 900 s — which is why 900 s would also need the
  backstop raised); sweep has its own bound `T + S`.

## 4. Misconfiguration catalogue

| Misconfiguration | Symptom | Guard in design |
|---|---|---|
| Pi sends new provisioning bits to pre-CART firmware | Node silently becomes always-on, drains battery | Caps join marker; Pi sends bit1 only when caps ≥ 1 (§3.1) |
| Mixed Phase-1 / CART firmware in one fleet | 29-byte beacons ignored by old nodes → partitions | Flag-day reflash; `selftest.sh` + app flag nodes still advertising 27-byte beacons |
| Different `MESH_SLEEP_INTERVAL_MS` on parent and child (e.g. 60 s test build vs production) | Child never rendezvous | Child adopts the parent's advertised T (`beacon_interval_ms`), sanity-bounded to 30 s…3600 s; mismatch logged |
| `G_max < 2·|b|·T` | Permanent miss/sweep loop (§3.3) | `static_assert(MESH_WAKE_GUARD_MAX_MS * 1000000ULL >= 2ULL * MESH_SLEEP_INTERVAL_MS * MESH_DRIFT_BIAS_PPM)` with `MESH_DRIFT_BIAS_PPM` set from Gate 0 |
| Relay awake budget above backstop | Watchdog cuts the window; children miss forever | Per-role backstop derived from the same constants (§3.7) with a `static_assert` |
| More than 6 children on one sleepy relay | RTC buffer overflow, drops, collision rate ↑ | Admission control via `RELAY_CAP` flag (§3.5) |
| `MESH_SLEEPY_RELAY_DEPTH_MAX` differs between nodes | Some refuse sleepy parents | Harmless (fewer relays); reported in `/mesh` telemetry as `sleepy_depth` |
| Node on a marginal RF link chosen as sleepy relay | Children miss repeatedly | Prefer candidates by (rank, guard hint, RSSI ≥ −80 dBm); leaf-only bit set at enrolment |
| `MESH_DEDUP_WINDOW_MS` ≥ T | Recycled seq dropped forever | Existing `static_assert` (30 s < 300 s holds) |
| ACK expected from a relayed leaf with store-and-forward | Every wake counted "unconfirmed" | Cut-through (§3.2); `g_unconfirmedWakes` semantics unchanged |

## 5. What stays bench-dependent

The relative bias `b`, the per-cycle step, the wake-latency spread, the UART
ACK round trip, and whether `RC_FAST_D256` materially beats the 136 kHz RC
on these boards. Every constant above is either a formula of those or a
simulated consequence of them; `cart_sim.py` is re-run with the measured
values before any constant is committed (plan Task 3).

## 6. Go / no-go gates

| Gate | Pass criteria |
|---|---|
| **0 — drift bench** | With measured b and step at the chosen T: simulated miss ≤ 1 %, sweeps ≤ 12/node/year, `G_max ≤ 20 s`, relay worst-case awake ≤ its backstop. If the 136 kHz RC fails and RC_FAST_D256 passes, adopt it (+0.12 mAh/day). If both fail: **no-go** — CART is not viable without an external crystal; keep Phase 1 and use mains/solar-boosted always-on relays. |
| **1 — parity** | `MESH_SLEEPY_RELAY_DEPTH_MAX = 0` build: 24 h bench, readings/ACK/mesh telemetry identical to Phase 1. |
| **2 — isolated pair** | One sleepy relay + one sleepy leaf out of bridge range (`MESH_TEST_IGNORE_BRIDGE`), ≥ 500 cycles: catch ≥ 99 %, zero lost readings, measured awake times within ±20 % of §3.4, every reading ACKed in-cycle. |
| **3 — fleet** | 7 days, all nodes: same criteria per node; no node in a sweep loop. |

## Sources

- ESP-IDF System Time (ESP32-C3): RTC_SLOW_CLK sources and stability — https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/system/system_time.html
- ESP-IDF Clock Tree (ESP32-C3) — https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/peripherals/clk_tree.html
- ESP32 forum, deep-sleep drift reports — https://www.esp32.com/viewtopic.php?t=22494 , https://www.esp32.com/viewtopic.php?t=2403 , https://esp32.com/viewtopic.php?t=7409
- arduino-esp32 issue #572, deep sleep timer accuracy — https://github.com/espressif/arduino-esp32/issues/572
- Espressif ESP-FAQ, ESP-NOW default rate 1 Mbps — https://docs.espressif.com/projects/esp-faq/en/latest/application-solution/esp-now.html
