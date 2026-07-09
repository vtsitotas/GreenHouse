# Dynamic Multi-Hop Mesh Relay for ESP-NOW Sensor Nodes — Design Spec

**Date:** 2026-07-09
**Status:** Approved, ready for implementation planning

## Background

Today's sensor network is a pure star topology: every edge node
(`firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino`,
`firmware/edge_node_esp32/edge_node_esp32.ino`) has a single hardcoded bridge
MAC and sends `SensorPacket` directly to it via ESP-NOW; the bridge
(`firmware/bridge_esp32/bridge_esp32.ino`) maps sender MAC → zone via a
hardcoded `ZONES[]` array and publishes to MQTT. There is no relaying, no
routing, and no encryption (`peer.encrypt = false`). This was flagged as a
known gap in `HANDOFF.md`'s backlog ("Multi-hop sensor mesh / relay bridging
for far-away nodes... not started") and in `docs/ARCHITECTURE.md`'s dotted
future-line.

This spec designs a dynamic multi-hop relay so a sensor node too far from the
bridge to reach it directly can route its data through nearer trusted nodes,
hop by hop, until it reaches the bridge — and so the network keeps working
(re-routes) if an intermediate node dies, moves, or its link degrades.

Current edge-node firmware does not implement deep sleep yet (it loops with
`delay(SEND_INTERVAL_MS)`, radio always on); deep sleep is documented as
separate future work in `docs/EDGE_NODE_POWER_OPTIMIZATION.md`. This design is
built to remain correct once that ships (the wire format carries the fields a
future deep-sleep scheduler needs), but the deep-sleep wake-scheduling logic
itself is out of scope here — see Non-goals.

**Methods considered and ruled out** (research summarized for the record —
see conversation for full comparison):
- **Preamble-sampling MAC (X-MAC/ContikiMAC/WiseMAC)** — mismatched to this
  hardware. These protocols assume microsecond-to-low-millisecond wake costs;
  ESP32-C3 deep-sleep-to-`app_main` alone is ~140–230ms before radio init.
  Frequent independent sampling would burn more energy waking up than it saves.
- **Synchronized flooding (Glossy/LWB/Chaos)** — needs sub-millisecond,
  interrupt-level time sync below what Arduino/ESP-NOW's API exposes.
- **Bluetooth-Mesh Friend/LPN hierarchy** — most power-efficient option that
  exists, but requires asymmetric roles (dedicated always-listening Friend
  nodes); contradicts the chosen "any node can relay" topology.
- **RPL (RFC 6550)** — not an alternative but the reference this design
  borrows from: strict rank-ordering for loop safety, and Trickle-timer-style
  adaptive control-message frequency.

## Goals

1. A sensor node that cannot reach the bridge in one ESP-NOW hop can still get
   its data to the bridge via one or more intermediate trusted nodes.
2. Routing is dynamic: nodes pick their next hop by lowest advertised
   hop-count ("rank"), RSSI as tiebreak, and self-heal (re-route) if their
   current parent goes silent.
3. Any trusted node can act as a relay for any other trusted node (full mesh,
   not restricted to designated/mains-powered relay nodes).
4. Only pre-provisioned, trusted nodes are considered as routing candidates or
   accepted as data sources; sensor-data payloads are encrypted in transit.
5. The wire protocol and timing model remain correct once deep sleep is added
   to edge nodes in a future slice (shared wake-window fields exist now, even
   though nothing schedules against them yet).
6. Routing loops are structurally prevented, not just bounded by a TTL.
7. The bridge can tell the difference between "a node is offline" and "a node
   is fine but its data is arriving via a longer path" — today it only ever
   publishes `online`, never anything on node silence.

## Non-goals

- **Implementing deep sleep itself.** That's `docs/EDGE_NODE_POWER_OPTIMIZATION.md`'s
  job, tracked separately. This design's wake-window/schedule fields are
  forward-compatible placeholders; validation happens against today's
  always-on nodes.
- **Per-pair unique encryption keys.** One shared network-wide PMK + LMK
  compiled into all firmware (matches the existing `ZONES[]`
  hardcoded-config pattern, just extended network-wide). This defends against
  a nearby stranger device injecting or reading sensor data; it does not
  defend against a physically-captured node's key being extracted. Documented
  limitation, acceptable for thesis scope.
- **Large-scale mesh.** Designed and bench-tested for a handful of nodes and
  up to ~4 hops, matching one greenhouse's actual node count. No attempt at
  a network that needs to scale beyond that.
- **Remote/dynamic reconfiguration of the trusted node list.** Adding a node
  still means adding it to a shared config header and reflashing — same
  process as today's `ZONES[]`, not a new pairing UI.
- **Automated firmware tests.** No automated test harness exists for the
  Arduino firmware today (`docs/ESP_NOW_BRIDGE_PROGRESS.md` — this has always
  been manual serial-monitor bench validation). This feature continues that
  pattern; see Testing section.

## Architecture

- The bridge is always rank 0 — it's mains-powered (plugged into the Pi) and
  always listening, so it's the network's timing anchor with no sync problem
  of its own.
- Every other trusted node tracks a **rank** (hop-count from the bridge) and a
  **parent** (the specific trusted neighbor it currently routes through).
  Rank = parent's rank + 1. A node with no valid parent is **unrouted**
  (rank = 255, sentinel).
- Nodes discover neighbors and ranks via periodic **beacons** (ESP-NOW
  broadcast, cleartext — broadcast frames cannot be encrypted, confirmed
  platform limitation). A beacon only reveals `{mac, rank, seq, timing
  fields}` — no sensor data.
- **Parent selection rule (RPL-style, loop-safe by construction):** a node may
  only select a parent whose advertised rank is *strictly less than* its own
  current rank. Among valid candidates, pick the lowest rank; break ties by
  RSSI. This makes a routing loop structurally impossible — a node can never
  route through something at or below its own rank. TTL (below) remains as a
  cheap backstop, not the primary defense.
- **Parent loss / self-healing:** if a node hears no beacon from its current
  parent for `PARENT_TIMEOUT` (3× that parent's last-advertised beacon
  interval), it drops the parent, reverts to unrouted, and re-enters
  discovery (a longer listen window to find any new valid parent).
- **Beacon frequency — Trickle-style adaptive backoff:** a node's beacon
  interval starts at `BEACON_INTERVAL_MIN` (2s) and doubles after each
  interval with no topology change (no rank change, no new/lost neighbor),
  capped at `BEACON_INTERVAL_MAX` (60s). Any change (rank changes, parent
  lost, new neighbor seen) resets it to `BEACON_INTERVAL_MIN` immediately.
  This means a settled network costs far less airtime/battery over time than
  one still forming or actively re-routing — directly answering the
  "reliability over time" concern: cost is proportional to instability, not
  wall-clock time. The bridge itself just beacons at a fixed short interval
  always; it's mains-powered, so there's no cost pressure to optimize there.
- **Shared wake-window (forward-compat only):** the bridge's beacon carries a
  `window_duration_ms` value; every relay copies the bridge's value into its
  own beacon so it propagates network-wide. Once deep sleep ships, all nodes
  would wake at a shared scheduled epoch and stay awake for this buffer,
  letting a packet cascade through several hops within one shared window
  without per-node timing math. Today, with no deep sleep, this field is
  carried but unused — nodes are already always listening.

## Trust & Security Model

- New shared header `firmware/mesh_config.h`, included by all three sketches
  (bridge + both edge node variants) — single source of truth, matching and
  extending today's `ZONES[]` pattern:
  - `MESH_PMK` (16 bytes) — ESP-NOW primary master key, shared network-wide.
  - `MESH_LMK` (16 bytes) — shared local master key (one network-wide key,
    not per-pair — see Non-goals).
  - `TRUSTED_NODES[]` — `{mac, zone_name}` for every real node in the
    network (supersedes `ZONES[]`; the bridge is included with `zone_name =
    nullptr`/unused).
- On boot, every node registers every *other* entry in `TRUSTED_NODES[]` as
  an ESP-NOW encrypted peer (`encrypt = true`, `lmk = MESH_LMK`) — needed
  because any trusted node might dynamically become anyone else's parent at
  runtime, so encrypted-peer relationships must exist network-wide up front,
  not just node-to-bridge.
- Beacons are broadcast and therefore always cleartext (platform limitation).
  A node **ignores beacons from any MAC not in `TRUSTED_NODES[]`** — an
  unlisted device can never be selected as a parent, regardless of the rank
  or RSSI it advertises.
- Actual sensor-data packets are unicast to the chosen parent and encrypted
  via the registered PMK/LMK peer relationship.

## Packet Formats

`SensorPacket` (existing, unchanged — `temperature`, `humidity`,
`soil_moisture`) stays as the payload, wrapped rather than modified, so no
working code needs to change shape:

```cpp
// Broadcast, cleartext. Neighbor discovery + rank advertisement only.
typedef struct {
  uint8_t  magic;                    // protocol version/sanity marker
  uint8_t  mac[6];                   // sender's own MAC
  uint8_t  rank;                     // sender's current rank (255 = unrouted)
  uint16_t seq;                      // monotonic per-sender counter
  uint32_t beacon_interval_ms;       // sender's current trickle interval (informational)
  uint32_t window_duration_ms;       // bridge-originated, propagated hop-by-hop
} MeshBeacon;

// Unicast to chosen parent, ESP-NOW encrypted (PMK/LMK).
typedef struct {
  uint8_t      magic;
  uint8_t      origin_mac[6];        // the node the reading is *from* (not the relay hop)
  uint8_t      origin_rank;          // origin's rank at send time (diagnostics only)
  uint8_t      ttl;                  // hard backstop; decremented per hop, dropped at 0
  uint16_t     seq;                  // per-origin monotonic counter, for de-dup
  SensorPacket payload;               // unchanged existing struct
} MeshDataPacket;
```

## Routing & Forwarding Behavior (every non-bridge node)

1. On boot: unrouted (rank 255), start in discovery mode (long listen window).
2. On hearing a beacon from a `TRUSTED_NODES[]` MAC with `rank < my rank`:
   consider it a parent candidate; pick lowest-rank (RSSI tiebreak); adopt if
   better than current parent.
3. On sensor-read interval: build a `MeshDataPacket` with `origin_mac = self`,
   `ttl = MESH_MAX_TTL` (4), fresh `seq`, unicast to current parent.
4. On receiving a `MeshDataPacket` addressed to self (i.e., some child picked
   this node as parent): if `ttl == 0` or `(origin_mac, seq)` already in the
   local de-dup cache, drop it. Otherwise forward it to *my own* parent with
   `ttl - 1`, and record `(origin_mac, seq)` in the de-dup cache (small ring
   buffer, ~32 entries).
5. If no valid parent exists (isolated): buffer own readings locally (ring
   buffer, most-recent 10, oldest dropped when full — not persisted across
   reboot) and retry once a parent is found, rather than silently dropping
   the first attempt.
6. Own beacon: broadcast `{mac, rank, seq, beacon_interval_ms,
   window_duration_ms}` on the current trickle interval (see Architecture).

## Bridge Changes (`firmware/bridge_esp32/bridge_esp32.ino`)

- Continuously broadcast its own `MeshBeacon` with `rank = 0` at a fixed short
  interval (no trickle backoff needed — mains-powered).
- Register every `TRUSTED_NODES[]` entry as an encrypted ESP-NOW peer at boot
  (same as every other node).
- On receiving a `MeshDataPacket`: look up zone by **`origin_mac`**, not the
  immediate ESP-NOW sender MAC (which may now be a relay, not the origin) —
  this is the one functional behavior change from today's direct-sender
  lookup.
- Track last-seen timestamp per `origin_mac`. Publish
  `greenhouse/nodes/<mac>/status = "offline"` once a node has missed
  `OFFLINE_AFTER` (e.g. 3×) its expected report interval — fixes the existing
  gap where only `"online"` is ever published, never anything on silence.
- Publish sensor-reading topics with `retain = true` (currently not set) —
  closes the existing `HANDOFF.md` backlog item about zone cards showing
  empty after a broker restart, bundled here since it's the same publish
  call site being touched.

## Fault Handling & Reliability Summary

| Fault | Behavior |
|---|---|
| Parent stops beaconing | Drop parent after `PARENT_TIMEOUT`, re-enter discovery |
| No trusted neighbor in range | Buffer readings locally, retry each cycle, don't crash |
| Two nodes transiently both think the other is a valid parent | Structurally prevented — strict rank rule means neither can ever pick the other (would require rank < its own) |
| A relay accidentally re-forwards the same packet twice (route flap) | De-dup cache (origin_mac, seq) drops the repeat |
| A packet loops somehow anyway (defense in depth) | TTL hard-drops after 4 hops |
| A node goes offline | Bridge publishes `offline` status after missed-report threshold, instead of staying silent forever |
| Untrusted device broadcasts beacons nearby | Ignored outright — not in `TRUSTED_NODES[]` |
| Untrusted device tries to send data | Rejected — no matching encrypted-peer relationship exists |

## Testing / Validation

No automated firmware test harness exists in this project; this continues
that established pattern (manual bench validation via serial monitor,
consistent with `docs/ESP_NOW_BRIDGE_PROGRESS.md`). Bench plan:

1. Bridge + 2 edge nodes, direct range — confirm rank-1 behavior unchanged
   from today (baseline regression check).
2. Force a 2-hop topology (physically separate a third node beyond direct
   bridge range, or temporarily lower its TX power) — confirm data arrives at
   the bridge via the intermediate node, with correct `origin_mac` zone
   lookup.
3. Power off the intermediate (rank-1) node mid-test — confirm the rank-2
   node detects parent loss and re-routes (or goes isolated/buffers if no
   alternate path exists).
4. Confirm `greenhouse/nodes/<mac>/status` transitions to `offline` after the
   removed node's missed-report threshold.
5. Confirm an unregistered ESP32 (not in `TRUSTED_NODES[]`) running the same
   beacon logic is never selected as anyone's parent.

## Files Touched

| File | Change |
|---|---|
| `firmware/mesh_config.h` (new) | Shared PMK/LMK, `TRUSTED_NODES[]`, tuning constants |
| `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` | Replace direct-to-bridge send with mesh routing (rank tracking, parent selection, beacon tx/rx, relay forwarding, trickle timer, local buffering) |
| `firmware/edge_node_esp32/edge_node_esp32.ino` | Same changes, WROOM variant |
| `firmware/bridge_esp32/bridge_esp32.ino` | Rank-0 continuous beacon, encrypted-peer setup for all trusted nodes, origin-based zone lookup, offline-status publishing, `retain=true` |

## Placeholder / Consistency Check

No TBDs remain. Tuning constants (`BEACON_INTERVAL_MIN/MAX`, `PARENT_TIMEOUT`,
`MESH_MAX_TTL`, `OFFLINE_AFTER`) are given concrete starting values above;
the implementation plan may adjust them if bench testing shows otherwise, but
they are not left open.
