# Mesh Field Visualization (Live Topology Map in the App) — Design Spec

**Date:** 2026-07-26
**Status:** Approved, ready for implementation planning
**Companion spec:** `2026-07-26-mesh-deep-sleep-design.md` (defines the
firmware/MQTT telemetry this screen consumes — see its §Telemetry)

## Background

The app's Devices screen (`app/lib/screens/devices/devices_screen.dart`) is
a flat `ListView` of nodes: online badge, battery %, last-seen time. It says
nothing about the *shape* of the network — who routes through whom, how
strong each link is, which nodes are asleep — even though the mesh firmware
now knows all of it and (per the companion spec) publishes it retained on
`greenhouse/nodes/<MAC>/mesh`, `/battery`, and `/status`.

This spec designs a **live mesh map screen**: every sensor drawn as an icon
card on a pannable/zoomable field canvas, connected to its parent by an
animated link, with RSSI, battery, zone, online state, and sleepy state
visible at a glance — updating in place as MQTT events arrive (parent
switches re-draw the link, RSSI recolors it, offline greys the node).

**Platform decision:** a Flutter screen in the existing app (not a Pi web
page). Rationale: the MQTT pipeline (`MqttConnection` →
`GreenhouseRepository` → Riverpod providers) already delivers node events
both on the LAN and remotely via HiveMQ; a portal page would be LAN-only
and a second UI stack to maintain.

**Layout decision:** automatic rank-layered layout (bridge at top, children
below by hop count) with **drag-to-pin**: any node can be dragged to its
real position in the greenhouse and stays there (persisted locally);
unpinned nodes keep auto-arranging. This gives zero-setup usefulness on day
one and a "real field map" once the user pins nodes where they physically
sit.

## Goals

1. A new **Mesh Map** screen reachable from the Devices screen, showing one
   node card per known device (bridge included) and one link per
   child→parent edge, live-updated from the repository's node stream.
2. Each node card shows: role icon (bridge / sensor), zone name (fallback:
   short MAC), online/offline color, battery icon + percent (when known),
   sleepy indicator (moon badge), and its parent-link RSSI in dBm.
3. Links are drawn child→parent, colored by link quality (good ≥ −60 dBm,
   fair ≥ −75, weak < −75; measured-at-child, from `/mesh`), with a subtle
   animated "flow" toward the parent to convey data direction. Offline
   nodes keep their last-known link, greyed and dashed.
4. Topology changes animate: a parent switch smoothly re-attaches the link;
   node cards ease to new layout positions; new nodes fade in.
5. Auto-layout by mesh rank; any node draggable, dragged position pinned and
   persisted across app restarts (per-node, local to the device);
   long-press unpins back to auto-layout. Canvas pans and zooms
   (`InteractiveViewer`).
6. Works **today against the simulator** (before the firmware slice ships or
   is even flashed): `pi/tools/simulator.py` is extended to publish the same
   `/mesh` contract with a small fake topology that shifts occasionally —
   so the whole screen is demonstrable end-to-end with zero hardware.
7. Fully unit/widget-tested per the repo's TDD convention for Dart code
   (models, topic routing, repository merge, layout math, link-color rules,
   screen smoke test).

## Non-goals

- **No Pi/portal web version.** (Decision above; the MQTT contract makes one
  possible later without firmware changes.)
- **No historical topology playback** — live + last-retained state only. The
  recorder is untouched; `/mesh` is not written to the history DB in this
  slice.
- **No editing of the mesh from the app.** Read-only visualization; no
  forcing parents, no renaming, no removing nodes.
- **No background floor-plan image upload.** Pinning happens on a plain
  field canvas; a photo/plan underlay is a clean future addition (position
  data model already supports it — normalized coordinates).
- **No cross-device sync of pinned positions** (SharedPreferences only, same
  as other app-local prefs). Retained MQTT config sync could come later.
- **No firmware changes in this slice.** The app consumes the contract; the
  firmware side ships via the companion plan. Until real nodes publish
  `/mesh`, real deployments show the map from `/status`+`/battery` only
  (cards without links) — acceptable and explicitly handled (see §Degraded
  data).

## Architecture

Data flows through the existing pipeline; every layer gets one additive
change:

```
MQTT greenhouse/nodes/<id>/mesh   (retained JSON, bridge- or simulator-published)
  → MqttConnection._route()            NEW: isNodeMeshTopic → NodeStatus.fromMqttMesh
  → GreenhouseRepository._handle()     existing NodeStatus merge (copyWith extended)
  → nodesProvider (unchanged)          Map<String, NodeStatus>
  → MeshMapScreen                      NEW: layout + painter + cards
```

### Model — extend `NodeStatus` (no new event type)

`NodeStatus` already merges `/status` and `/battery` events per `nodeId` in
the repository (`greenhouse_repository.dart:101-110`). Mesh info follows the
identical pattern — new nullable fields, new factory, extended `copyWith`
merge:

```dart
class NodeStatus {
  final String nodeId;          // 12-hex MAC (real fleet) or sim name
  final bool isOnline;
  final double? batteryPercent;
  final DateTime lastSeen;
  // NEW (all nullable — absent until a /mesh message arrives):
  final String? parentId;       // null = root/bridge or unknown
  final int? meshRank;          // 0 = bridge
  final int? parentRssi;        // dBm at the child end of the link
  final bool? isSleepy;
  final String? zone;           // from /mesh payload (bridge knows TRUSTED_NODES)
  final int? batteryMv;
  ...
  factory NodeStatus.fromMqttMesh(String nodeId, String jsonPayload) ...
}
```

Parse defensively (try/catch → ignore malformed payload, same as every
other JSON topic in `_handle`). `"parent": null` (the bridge) maps to
`parentId == null` with `meshRank == 0` — the screen tells "root" from
"unknown" via `meshRank`.

### Topic routing

`MqttConnection`: add
`isNodeMeshTopic` (`^greenhouse/nodes/[^/]+/mesh$`) and a `_route` branch
emitting `NodeStatus.fromMqttMesh(extractNodeId(topic), payload)` — mirror
of the existing battery branch (`mqtt_connection.dart:159-160`). A `/mesh`
arrival also implies liveness, but **does not** set `isOnline` (that stays
`/status`-owned; the factory emits `isOnline: true` like the battery factory
does, and the repository merge keeps the last explicit status — see merge
rule below).

Repository merge rule (extended `copyWith` call at
`greenhouse_repository.dart:103`): mesh fields overwrite only when the
incoming event carries them (`?? prev.x` pattern, exactly like
`batteryPercent` today).

### Screen structure

```
app/lib/screens/devices/mesh_map_screen.dart      Scaffold + InteractiveViewer
app/lib/screens/devices/mesh_map/
  mesh_layout.dart        pure layout engine (testable, no Flutter deps)
  mesh_link_painter.dart  CustomPainter: links, colors, dash/flow animation
  mesh_node_card.dart     one node's visual card (icon/zone/battery/rssi/badges)
  node_positions_store.dart  pinned-position persistence (SharedPreferences)
```

- **Canvas:** fixed logical size (e.g. 1200 × 1600), wrapped in
  `InteractiveViewer` (pan + pinch-zoom, `minScale 0.5 / maxScale 3`).
- **Layer order:** links painted first (`CustomPaint` sized to the canvas),
  node cards stacked above at their positions (`Positioned` inside a
  `Stack`), each wrapped in a `GestureDetector` for drag/long-press.
- **Live updates:** the whole subtree watches `nodesProvider`; layout
  recomputes on every emission; `AnimatedPositioned` (250 ms ease) moves
  cards; the painter animates a dash-offset via a single repeating
  `AnimationController` (~1.5 s cycle) for the flow effect — links
  themselves are repainted with new endpoints/colors on data change.

### Layout engine (pure Dart, unit-tested)

Input: `Map<String, NodeStatus>` + pinned positions. Output:
`Map<String, Offset>` (normalized 0–1, scaled by the canvas at paint time).

1. Partition nodes into **rank rows**: row 0 = rank 0 / the node every
   parentless chain points to (bridge), then rank 1, rank 2, … Nodes with
   no `/mesh` data yet (rank unknown) go in a bottom "unplaced" row.
2. Row y = `0.12 + row * 0.18` (clamped); within a row, spread x evenly
   ordered by a stable key (zone name, else nodeId) — stable ordering
   prevents cards jumping when unrelated events arrive.
3. Pinned nodes: their stored position wins, they're excluded from row
   spreading. Links always follow actual positions, pinned or not.
4. Cycle/orphan safety: `parentId` pointing at an unknown node draws the
   card in the unplaced row with a dangling greyed link stub — never throws.

### Link quality mapping (pure function, unit-tested)

| RSSI (dBm, at child) | Color (theme-aware) | Meaning |
|---|---|---|
| ≥ −60 | `AppColors.online` green | good |
| −61 … −75 | amber | fair |
| < −75 | red/orange | weak |
| node offline / rssi null | grey, dashed | stale/last-known |

Label: small `-61 dBm` text at the link midpoint (painter-drawn
`TextPainter`, skipped below a zoom threshold to avoid clutter).

### Node card

- Icon: `Icons.router` (bridge, rank 0) / `Icons.sensors` (sensor node);
  icon color = online/offline (`AppColors.online` / `.offline`, matching
  `NodeListTile`).
- Title: `zone` if known, else last-4-of-MAC; subtitle: short MAC.
- Chips row: battery icon + `%` (reuse `NodeListTile._batteryIcon` logic —
  extract it to a shared helper rather than duplicating), RSSI `-61 dBm`,
  moon icon 🌙 badge when `isSleepy == true` (tooltip "Deep-sleep node"),
  small `zZ`-style dimming of the whole card when sleepy *and* between
  reports.
- Offline: card greyscaled at reduced opacity + `Offline` pill (same pill
  style as `NodeListTile`).
- Tap: bottom sheet with full details (MAC, rank, parent MAC, RSSI, battery
  mV + %, last seen, zone) — reuses data already on `NodeStatus`, no new
  plumbing.

### Pinned positions

`node_positions_store.dart`: `SharedPreferences` key
`mesh_map_positions_v1` → JSON `{ "<nodeId>": {"x":0.31,"y":0.62}, ... }`
(normalized coords, so canvas-size changes don't invalidate pins). API:
`load()`, `pin(nodeId, offset)`, `unpin(nodeId)`. Drag-end pins; long-press
→ confirm-dialog unpin. Provider-wrapped so the screen stays declarative.

### Entry point

Devices screen `AppBar` gains an action icon (`Icons.hub`) → pushes
`MeshMapScreen`. (Devices stays the default list view — the map is a
companion, not a replacement.)

### Degraded data (real fleet before firmware slice ships)

With only `/status` + `/battery` retained (today's real bridge): all nodes
land in the "unplaced" row as cards with battery/online state and no links.
The screen shows an unobtrusive hint banner ("Mesh topology data not being
published yet — update bridge/node firmware") when ≥ 1 node is online but
none has mesh info. No crashes, no empty screen.

## Simulator extension (so the screen is buildable now)

`pi/tools/simulator.py` additions (kept boring, matching its style):

- `BRIDGE = "A0B1C2D3E4F5"`; publish its `/mesh`
  (`parent: null, rank: 0, sleepy: false`) and `/status online` retained at
  start.
- Static base topology for the existing `NODES`: `node1 → bridge` (rank 1),
  `node2 → node1` (rank 2), `node3 → bridge` (rank 1); `node2` marked
  `sleepy: true`.
- Every publish cycle: RSSI per node = slow random walk in −45…−85; battery
  mv derived from the existing pct decay; every ~10 cycles `node3` flips
  parent between `bridge` and `node1` (rank 1 ↔ 2) — exercises the re-link
  animation.
- All `/mesh` publishes retained, `qos=1`, JSON exactly per the contract
  table in the companion spec §Telemetry.

## Testing (repo TDD convention applies — Dart and Python)

- `app/test/models/node_status_test.dart`: `fromMqttMesh` parse (full
  payload, nulls, malformed JSON, bridge record), `copyWith` mesh-field
  merge.
- `app/test/connection/mqtt_connection_test.dart`: `isNodeMeshTopic`
  matrix, routing emits the mesh-flavored `NodeStatus`.
- `app/test/repository/greenhouse_repository_test.dart`: `/status` +
  `/battery` + `/mesh` events for one node merge into a single complete
  entry; `/mesh` doesn't clobber a newer explicit offline status.
- `app/test/widgets/mesh_layout_test.dart`: rank rows, stable ordering,
  pinned override, unknown-parent safety, unplaced row.
- `app/test/widgets/mesh_link_painter_test.dart`: RSSI→color/dash pure
  function matrix.
- `app/test/widgets/mesh_map_screen_test.dart`: pumps the screen with a
  faked provider — cards render with zone/battery/RSSI/moon badge, offline
  node greyed, hint banner in degraded mode.
- `pi/tests/test_simulator_mesh.py` (or extend an existing test module):
  the topology payload builder emits contract-shaped JSON (factor the
  payload construction into a testable function).
