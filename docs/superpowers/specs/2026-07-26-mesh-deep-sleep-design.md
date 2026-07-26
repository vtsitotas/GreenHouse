# Deep Sleep for Mesh Edge Nodes (Phase 1: Leaf Sleep) + Mesh Telemetry — Design Spec

**Date:** 2026-07-26
**Status:** Approved, ready for implementation planning
**Companion spec:** `2026-07-26-mesh-field-visualization-design.md` (consumes
the MQTT telemetry contract defined here)

## Background

The dynamic multi-hop mesh
(`docs/superpowers/specs/2026-07-09-dynamic-mesh-relay-design.md`,
implemented in `firmware/libraries/GreenhouseMesh/`) deliberately left deep
sleep out of scope. Edge nodes today loop with the radio always on, read
sensors every 5 s, and beacon on a trickle schedule — ~100 mA continuous
draw. `docs/EDGE_NODE_POWER_OPTIMIZATION.md` shows that a 15-minute
sleep/wake duty cycle drops the average draw to ~290 µA, making an 18650
LiFePO4 + small solar panel self-sustaining (200+ days of darkness).

The mesh was built to be forward-compatible with sleep: every `MeshBeacon`
already carries `window_duration_ms` (carried, unused), and `TODO.md §2`
records a worked-out clock-synchronization design for fully-synchronized
wake windows. That full design is the end goal, but it is the riskiest part
to get right on real hardware (a node with a drifted clock simply vanishes),
and this fleet has never even bench-run the mesh itself yet (`TODO.md §3`).

**Decision: stage it.** This spec covers **Phase 1 — leaf sleep**: nodes
designated as battery-powered ("sleepy") deep-sleep between readings and
never act as relays; mains/solar-powered nodes and the bridge stay always-on
and carry all relay duty. Phase 2 (synchronized wake windows so *every* node
can sleep and still relay) is sketched in §Phase 2 but explicitly not
designed in detail here — it needs real per-board RTC-drift measurements
from Phase 1 field time first.

This spec also defines the **mesh telemetry** additions (battery voltage,
parent MAC, parent RSSI on the wire; battery % and topology JSON on MQTT)
because they ride the same wire-format change and the same one-shot fleet
reflash. The visualization spec consumes the MQTT contract defined in
§Telemetry.

## Goals

1. A node designated as sleepy spends ≥ 99.5 % of wall-clock time in deep
   sleep at the target 15-minute reporting interval, waking only to read
   sensors and hand one reading (plus any backlog) to its parent.
2. Sleepy nodes still participate in the mesh as **children**: they pick a
   parent with the same strict-rank/RSSI rule, can reach the bridge through
   multi-hop relays, and re-route if their remembered parent is gone.
3. Sleepy nodes are **never chosen as parents**: their beacons are marked and
   ignored for parent selection, so no packet is ever routed *through* a node
   that is about to turn its radio off.
4. A sleepy node's readings survive its own isolation: the existing
   10-reading buffer persists across deep sleep (RTC memory), and per-origin
   `seq` numbers keep counting across sleep cycles so the bridge's de-dup
   cache doesn't eat the first packets after a wake.
5. The bridge distinguishes "sleepy node between wakes" (normal) from "sleepy
   node dead" (offline), using a per-node expected-report interval instead of
   the current global 5 s one.
6. Every data packet carries battery voltage, the origin's current parent,
   and the RSSI the origin measured for that parent; the bridge republishes
   these as retained MQTT topics (contract in §Telemetry) for the app.
7. Wake cost is bounded: a wake with a healthy remembered parent completes in
   ~2.5–3.5 s (dominated by sensor warm-up, done concurrently with radio
   work); a wake with a dead parent is capped by a discovery window plus a
   hard max-awake backstop, then sleeps regardless (readings buffered).

## Non-goals

- **Phase 2 synchronized wake windows.** No relay-capable sleeping in this
  slice. See §Phase 2 for the recorded direction; do not build it yet.
- **Runtime role changes.** Sleepy vs always-on is a compile-time flag per
  node in `TRUSTED_NODES[]` (same add-a-node-and-reflash model as MACs and
  zones). No OTA/remote toggling.
- **Battery charging telemetry / solar MPPT stats.** Only battery voltage is
  measured (one ADC pin + resistor divider). Charge current, panel voltage
  etc. are hardware not present on the boards.
- **Backward wire compatibility.** `MeshBeacon` grows to 19 bytes and
  `MeshDataPacket` to 33 bytes; the whole fleet (bridge + both edge
  variants) is reflashed together, exactly like the original mesh rollout
  (plan `2026-07-09-dynamic-mesh-relay.md`, Global Constraints).
- **Automated firmware tests.** Same manual serial-monitor bench validation
  as every firmware slice (see Testing).
- **The hardware mods themselves** (LED desolder, LDO bypass, divider
  soldering) — listed as bench-test prerequisites, not designed here; see
  `docs/EDGE_NODE_POWER_OPTIMIZATION.md §1B`.

## Architecture

### Roles

| Role | Power | Radio | May relay | May sleep | Config |
|---|---|---|---|---|---|
| Bridge | Pi USB (mains) | always on | is the root | never | `zone == nullptr` |
| Always-on edge | mains/solar-buffered | always on | yes | never | `sleepy == false` |
| Sleepy edge | battery (+solar) | on ~3 s per 15 min | **never** | yes | `sleepy == true` |

The role lives in `TRUSTED_NODES[]` (new `bool sleepy` field), so a single
firmware image serves both edge roles — each board looks up **its own MAC**
at boot and behaves accordingly. No per-sketch `#define` divergence.

### Why leaf-only sleep composes cleanly with the existing mesh

The current mesh has exactly one assumption that sleep would violate: *a
parent is continuously listening*. Phase 1 keeps that assumption true by
construction — anything that can be a parent never sleeps, and anything that
sleeps advertises itself as ineligible:

- **New beacon flag `MESH_FLAG_SLEEPY`.** `meshHandleBeacon()` gains one
  rule: a beacon with the sleepy flag set is recorded for neighbor liveness
  but is **never** a parent candidate (checked before the strict-rank rule).
  Everything else — strict rank, RSSI tiebreak, trickle resets, parent
  timeout, TX-failure backstop, de-dup, TTL — is untouched.
- Sleepy nodes still *send* beacons on wake (one per wake, flagged sleepy).
  This is deliberate: hearing a new neighbor trickle-resets awake nodes
  (`mesh_node.h:202`), so the sleepy node's wake beacon provokes nearby
  routed nodes into beaconing within ~2 s — which is exactly how the sleepy
  node refreshes its parent's RSSI/rank (or discovers a new parent) inside
  its short wake window without waiting out a 60 s trickle interval.
- Sleepy nodes never call `meshRelayData()` forwarding (they can't receive
  child unicasts anyway — nobody adopts them — but the code guard documents
  intent, same style as the existing trust check in `meshRelayData`).

### The sleepy wake cycle

Replaces the always-on `loop()` state machine **only when** the booting
node's own `TRUSTED_NODES[]` entry says `sleepy`. Always-on nodes keep
today's loop exactly (plus telemetry fields).

```
boot (deep-sleep timer wake, or first power-on)
 1. restore RTC state if valid (magic + wake-cause check):
      data seq, beacon seq, parent idx, wifi channel, buffered readings
 2. sensor GPIO power ON               ── warm-up clock starts (2000 ms)
 3. radio up: WIFI_STA, set channel from RTC state
      (first boot / invalid RTC state: full SSID channel scan, as today)
 4. esp_now init, meshInit(0), register callbacks
 5. send one sleepy-flagged beacon (rank from restored parent, else UNROUTED)
      → provokes trickle resets in earshot; listen runs concurrently
 6. wait out the remainder of sensor warm-up, handling beacons as they
      arrive (parent refresh / adoption via unchanged meshHandleBeacon)
 7. read DHT + soil, sensor GPIO power OFF
 8. if routed: meshSendReading() (flushes RTC-persisted backlog first,
      then today's reading); wait ≤ MESH_TX_CONFIRM_WAIT_MS for the send
      callback; 3-fail backstop drops the parent as today
 9. if unrouted (no remembered parent, or step 8 failed): keep listening
      up to MESH_WAKE_DISCOVERY_MS for an adoptable beacon, then retry
      step 8 once; still unrouted → reading stays in the RTC buffer
10. persist RTC state (seq counters, parent idx, channel, buffer)
11. esp_deep_sleep(MESH_SLEEP_INTERVAL_MS − time awake)   [≥ 1 s floor]
      hard backstop: MESH_WAKE_MAX_AWAKE_MS forces step 10–11 regardless
```

**Timing budget** (healthy path): boot ~0.2 s + warm-up 2.0 s (radio work
hidden inside it) + read/send/confirm ~0.3 s ≈ **2.5 s awake**, matching the
power doc's Scenario B math. Worst case (orphaned): + 5 s discovery + one
retry, hard-capped by `MESH_WAKE_MAX_AWAKE_MS` (10 s).

### What must survive deep sleep (RTC memory)

Deep sleep wipes normal RAM; all `static` mesh state in `mesh_node.h` is
reborn zeroed each wake. Four things must not be:

| State | Why it must persist | Where |
|---|---|---|
| `meshDataSeq` (+ beacon seq) | Bridge/relay de-dup is `(origin_mac, seq)`; a reset-to-0 seq after every sleep makes the first 32 readings collide with cached entries → silently dropped data | `RTC_DATA_ATTR` struct |
| Parent idx + its last rank | Skip discovery on the healthy path; wake→unicast directly | same |
| WiFi channel | Skip the ~2 s SSID scan every wake (huge energy cost at scale); re-scan only when orphaned (existing `MESH_RESCAN_AFTER_MS` intent, adapted) | same |
| Reading buffer (10 × `MeshDataPacket`) | Goal 4: isolation across sleep cycles must not lose data; ESP32-C3 has 8 KB RTC slow RAM, buffer is 330 B | same |

A `magic` field + `esp_sleep_get_wakeup_cause()` guard invalidates the
struct on first power-on / flash / brown-out, falling back to full
discovery. The parent is *remembered as a hint, not trusted*: the wake
beacon + warm-up listen refreshes it, and the normal TX-failure backstop
(3 fails → drop) catches a parent that died while we slept — with the wake
retry + buffer as the safety net.

### Bridge-side liveness with mixed cadences

`checkOfflineNodes()` currently uses one global
`MESH_EXPECTED_REPORT_INTERVAL_MS` (5 s). It becomes per-node: expected
interval = `MESH_SLEEP_INTERVAL_MS` for sleepy entries, else
`MESH_EXPECTED_REPORT_INTERVAL_MS`; offline after `MESH_OFFLINE_AFTER` (3) ×
that node's expected interval. So a sleepy node is offline after 45 min of
silence (3 missed wakes), an always-on node after 15 s — same rule, per-role
clock.

## Wire format changes (fleet reflash, one shot)

Grown at the **end** of each struct — existing field offsets unchanged.
Length still disambiguates message type (19 vs 33 bytes).

```c
// MeshBeacon — 18 → 19 bytes
typedef struct __attribute__((packed)) {
  uint8_t  magic;
  uint8_t  mac[6];
  uint8_t  rank;
  uint16_t seq;
  uint32_t beacon_interval_ms;
  uint32_t window_duration_ms;   // still carried, still unused (Phase 2)
  uint8_t  flags;                // NEW — bit0: MESH_FLAG_SLEEPY
} MeshBeacon;

// MeshDataPacket — 23 → 33 bytes
typedef struct __attribute__((packed)) {
  uint8_t      magic;
  uint8_t      origin_mac[6];
  uint8_t      origin_rank;
  uint8_t      ttl;
  uint16_t     seq;
  SensorPacket payload;
  uint16_t     battery_mv;       // NEW — 0 = not measured (mains node)
  uint8_t      parent_mac[6];    // NEW — origin's parent at send time
  int8_t       parent_rssi;      // NEW — RSSI origin last measured for that
                                 //       parent's beacon (dBm, -128 = n/a)
  uint8_t      flags;            // NEW — bit0: origin is sleepy
} MeshDataPacket;
```

`parent_mac`/`parent_rssi`/`flags` are set by the **origin** and, like
`origin_mac`, never rewritten by relays — the bridge learns every node's
own view of its uplink, which is exactly what the topology visualization
needs (each edge of the tree reported by its child end).

Battery sensing: resistor divider (2 × 220 kΩ, battery+ → ADC pin → GND,
~7.5 µA constant drain — accepted, it's 14 % of the 55 µA sleep floor and
avoids a high-side switch part) on a free ADC1 pin per board variant.
Averaged over 8 samples, converted with `analogReadMilliVolts()` (factory
ADC cal), doubled for the divider. Voltage→percent mapping happens **on the
bridge** (single tunable place, LiFePO4 curve is very flat — piecewise
table in the plan), not on the nodes.

## Telemetry — MQTT contract (consumed by the visualization spec)

All retained, all published by the bridge (except the bridge's own LWT):

| Topic | Payload | Notes |
|---|---|---|
| `greenhouse/nodes/<MAC>/status` | `online` / `offline` | existing, unchanged; `<MAC>` = 12 uppercase hex, no separators (`meshFormatMac`) |
| `greenhouse/nodes/<MAC>/battery` | float percent, e.g. `87.5` | NEW from real firmware (schema already used by `pi/tools/simulator.py` and parsed by the app). Published only when `battery_mv > 0`. |
| `greenhouse/nodes/<MAC>/mesh` | JSON, see below | NEW |

```json
{
  "parent": "206EF16C6B50",   // 12-hex MAC of current parent, null for the bridge
  "rank": 2,                  // origin_rank from the packet; 0 for the bridge
  "rssi": -61,                // parent_rssi; null if -128/bridge
  "sleepy": true,             // flags bit0
  "battery_mv": 3312,         // raw millivolts; null if 0
  "zone": "zone1",            // from TRUSTED_NODES[] (null for the bridge)
  "ts": 1753500000            // bridge uptime-derived epoch seconds omitted if unavailable; optional field
}
```

- Published on every data packet received from that origin (readings arrive
  at most every 5 s per node — no rate concern at this fleet size).
- The **bridge publishes its own** `/mesh` record at boot
  (`{"parent":null,"rank":0,"sleepy":false,...}`) and registers an MQTT LWT
  of `offline` on its own `/status` topic (plus `online` retained at
  connect), so the visualization can render the root and detect a dead
  bridge — today nothing reports bridge liveness at all.
- Retained ⇒ the app reconstructs the last-known topology immediately on
  connect, even between sleepy wakes.

## Config additions (`mesh_config.h`)

```c
#define MESH_FLAG_SLEEPY          0x01
#define MESH_SLEEP_INTERVAL_MS    900000UL  // 15 min (power doc Scenario B)
#define MESH_WAKE_DISCOVERY_MS    5000UL    // orphaned-wake listen window
#define MESH_TX_CONFIRM_WAIT_MS   500UL     // wait for esp-now send callback
#define MESH_WAKE_MAX_AWAKE_MS    10000UL   // hard backstop: sleep no matter what
#define MESH_MIN_SLEEP_MS         1000UL    // floor after subtracting awake time

struct TrustedNode {
  uint8_t     mac[6];
  const char* zone;
  bool        sleepy;   // NEW — battery node: leaf-only, deep-sleeps
};
```

15 min is the spec default per the power analysis; it is a single constant,
so per-deployment tuning stays a one-line change (the "configurable"
question was resolved: fixed 15 min default, no per-node intervals).

## Phase 2 (recorded direction — DO NOT BUILD YET)

For the record, so the thinking in `TODO.md §2` isn't lost: full
synchronized sleep would let relay nodes sleep too, by having every node
resync its clock **every wake** against its parent's beacon
(`beacon_interval_ms` = "gap until my next beacon" already on the wire),
cascading from the always-on bridge down the ranks; each wake = guard-listen
window sized to *one interval's* RTC drift (never cumulative) → hear parent
→ own TX + children's relay inside `window_duration_ms` → sleep until the
next computed window. Needs: measured per-board drift (Phase 1 field data
gives this for free by logging wake-time error), a `MESH_WAKE_GUARD_MS`
constant, and ideally 32 kHz crystal hardware. The Phase 1 wire format
already carries everything Phase 2 needs (`beacon_interval_ms`,
`window_duration_ms`, `flags`).

## Testing

No automated firmware harness (project convention). Bench plan (full
checklist in the implementation plan, Task 6):

1. All three device types reflashed; verify 19/33-byte sizes logged.
2. Sleepy C3 node: serial shows the wake cycle ≤ ~3.5 s, correct sleep
   duration math, seq numbers strictly increasing across ≥ 3 sleep cycles
   (watch the bridge log: no de-dup drops on post-wake packets).
3. Parent-of-sleepy-node powered off mid-sleep → next wake: TX fails,
   discovery window runs, node re-parents (or buffers) and still sleeps
   within the backstop cap.
4. Sleepy beacon ignored as parent: force the WROOM node unrouted
   (`MESH_TEST_IGNORE_BRIDGE`) while the sleepy C3 is awake — WROOM must
   NOT adopt it.
5. MQTT: `/battery` and `/mesh` retained topics appear with plausible
   values; `mosquitto_sub -t 'greenhouse/nodes/#'` while flipping topology.
6. Offline timing: sleepy node offline only after ~45 min silence;
   always-on node still ~15 s.
7. Multimeter/USB-meter sanity check of sleep current on the modified board
   (target: sub-mA on an unmodified board, ~55 µA modified).
