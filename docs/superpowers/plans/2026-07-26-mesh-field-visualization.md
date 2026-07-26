# Mesh Field Visualization (Live Topology Map) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A live "Mesh Map" screen in the Flutter app: every node (bridge +
sensors) as an icon card on a pannable/zoomable canvas, each connected to
its parent by an animated, RSSI-colored link, showing battery %, zone,
online/offline, and sleepy state — auto-laid-out by mesh rank, with
drag-to-pin persistence. Plus a simulator extension so the whole screen is
demonstrable with zero hardware.

**Architecture:** Additive change at every existing layer:
`greenhouse/nodes/<id>/mesh` retained JSON → new `MqttConnection` route →
`NodeStatus` gains nullable mesh fields (same merge pattern as battery) →
existing `nodesProvider` → new `MeshMapScreen` (pure-Dart layout engine +
`CustomPainter` links + card widgets + SharedPreferences position store).
Firmware is NOT in this plan (see `2026-07-26-mesh-deep-sleep.md`); Task 1
extends `pi/tools/simulator.py` to publish the contract so app work
proceeds against `mosquitto` + simulator immediately.

**Tech Stack:** Flutter/Dart (Riverpod, `shared_preferences`,
`InteractiveViewer`, `CustomPainter` — all already in the app; **no new
pub dependencies**), Python 3 + paho-mqtt (simulator), pytest.

**Reference spec:** `docs/superpowers/specs/2026-07-26-mesh-field-visualization-design.md`
(MQTT contract: companion spec `2026-07-26-mesh-deep-sleep-design.md` §Telemetry)

> **TDD applies** (Dart + Python — repo convention). Write the test first
> in every task that touches `app/lib` or `pi/`. Run with
> `flutter test` / `python -m pytest pi/tests` per the CI workflow.

## Global Constraints

- MQTT contract is fixed by the companion spec §Telemetry — do not invent
  fields; unknown fields in payloads must be ignored, absent ones → null.
- `nodeId` keys are whatever the topic segment says (12-hex MACs on the
  real fleet, `node1`-style names from the simulator) — never parse or
  validate MAC format in the app.
- No new pub dependencies; no changes to `MqttConnection`'s subscribe
  pattern (`greenhouse/#` already covers `/mesh`).
- `/mesh` events must never flip a node to online/offline on their own —
  `isOnline` stays owned by `/status` messages in the repository merge.
- All layout / color / persistence logic lives in pure-Dart files with no
  Flutter widget imports where feasible (testability).
- Screen must handle: zero nodes, nodes without mesh data (degraded-data
  banner per spec), unknown `parentId`, self-parent/cycle payloads
  (defensive — draw, never throw).
- Existing screens/tests keep passing untouched except `NodeListTile`'s
  battery-icon helper extraction (Task 4) and the Devices `AppBar` action
  (Task 6).

## File Structure

| File | Change |
|---|---|
| `pi/tools/simulator.py` | Mesh topology publishing (Task 1) |
| `pi/tests/test_simulator_mesh.py` | New (Task 1) |
| `app/lib/models/node_status.dart` | Mesh fields + `fromMqttMesh` (Task 2) |
| `app/lib/connection/mqtt_connection.dart` | `/mesh` routing (Task 2) |
| `app/lib/repository/greenhouse_repository.dart` | Extended merge (Task 3) |
| `app/lib/screens/devices/mesh_map/mesh_layout.dart` | New — layout engine (Task 4) |
| `app/lib/screens/devices/mesh_map/mesh_link_painter.dart` | New — links painter + quality mapping (Task 4) |
| `app/lib/screens/devices/mesh_map/mesh_node_card.dart` | New — node card widget (Task 4) |
| `app/lib/screens/devices/mesh_map/node_positions_store.dart` | New — pin persistence (Task 5) |
| `app/lib/screens/devices/mesh_map_screen.dart` | New — the screen (Task 5) |
| `app/lib/screens/devices/devices_screen.dart` + `node_list_tile.dart` | Entry point; battery-icon helper extraction (Tasks 4, 6) |
| `app/test/...` | New/extended tests per task |

---

### Task 1: Simulator publishes the mesh contract

**Files:** `pi/tools/simulator.py`, new `pi/tests/test_simulator_mesh.py`

Produces (consumed manually by every later task's "run it" check): retained
`greenhouse/nodes/<id>/mesh` for `A0B1C2D3E4F5` (bridge) + `node1..3`, per
spec §Simulator extension — base topology `node1→bridge(r1)`,
`node2→node1(r2, sleepy)`, `node3→bridge(r1)`; RSSI slow random walk
−45…−85; `battery_mv` derived from the existing pct decay
(`mv = 2800 + pct*6` is fine); `node3` re-parents every ~10 cycles.

- [ ] **Step 1 (test first):** factor payload construction into
  `build_mesh_payload(node, parent, rank, rssi, sleepy, battery_mv, zone) -> str`;
  tests: JSON round-trips, `parent`/`rssi`/`battery_mv` null handling,
  bridge record shape, key set matches the contract exactly.
- [ ] **Step 2:** implement + wire into the publish loop (retained, qos=1);
  bridge `/status online` + `/mesh` at start, alongside the existing node
  bootstrap block (`simulator.py:31-35`).
- [ ] **Step 3:** `python -m pytest pi/tests/test_simulator_mesh.py` green;
  manual: run sim against a local mosquitto,
  `mosquitto_sub -v -t 'greenhouse/nodes/#'` shows the contract.
- [ ] **Step 4:** Commit — `feat: simulator publishes mesh topology telemetry`

---

### Task 2: Model + topic routing

**Files:** `app/lib/models/node_status.dart`,
`app/lib/connection/mqtt_connection.dart`, tests
`app/test/models/node_status_test.dart` (new),
`app/test/connection/mqtt_connection_test.dart` (extend)

Produces: `NodeStatus` fields `parentId, meshRank, parentRssi, isSleepy,
zone, batteryMv` (all nullable) + `NodeStatus.fromMqttMesh(String nodeId,
String json)` + extended `copyWith`; `MqttConnection.isNodeMeshTopic`;
`_route` branch emitting the mesh-flavored `NodeStatus` (placed **before**
the `isSensorTopic` catch-all, beside the battery branch at
`mqtt_connection.dart:159`).

- [ ] **Step 1 (tests first):** `fromMqttMesh` — full payload, explicit
  nulls, malformed JSON (throws → caller catches; match the repo's
  try/catch-at-route style: have the factory throw `FormatException` and
  the `_route` branch wrap in try/catch like the `WeatherAlert` branch),
  bridge record (`parent:null`, `rank:0`); `copyWith` preserves mesh
  fields; `isNodeMeshTopic` matrix incl. `/mesh` vs `/meshx` vs deeper
  paths; routing test: publishing on a mesh topic emits `NodeStatus` with
  `parentId` set and `isOnline: true` (liveness-hint semantics, same as
  the battery factory).
- [ ] **Step 2:** implement; `flutter test` fully green (existing
  `node_status` expectations may need the new constructor params — keep
  them optional so no call sites break).
- [ ] **Step 3:** Commit — `feat: parse mesh topology topic into NodeStatus`

---

### Task 3: Repository merge

**Files:** `app/lib/repository/greenhouse_repository.dart`, extend
`app/test/repository/greenhouse_repository_test.dart`

- [ ] **Step 1 (tests first):** for one nodeId, deliver `/status online` →
  `/battery 88` → `/mesh {...}` events; final map entry carries all three
  facets. Then `/status offline` after `/mesh` → `isOnline` false but mesh
  fields retained. Then a fresh `/mesh` → does **not** resurrect
  `isOnline` true — assert the merge keeps explicit-status ownership.
  Merge rule to implement: add a `source` enum to `NodeStatus`
  (`NodeStatusSource.status | battery | mesh`, set by each factory), and
  switch the merge at `greenhouse_repository.dart:103-108` on it —
  `status` events own `isOnline`; `battery` and `mesh` events merge their
  own fields with `?? prev` fallbacks, update `lastSeen`, and take
  `isOnline` from `prev` when prev exists (a first-ever event may keep its
  factory default).
- [ ] **Step 2:** implement; full `flutter test` green.
- [ ] **Step 3:** Commit — `feat: merge mesh topology events into node state`

---

### Task 4: Layout engine, link painter, node card (pure pieces)

**Files:** the three new `mesh_map/` files (minus the store), shared
battery-icon helper, tests `app/test/widgets/mesh_layout_test.dart`,
`app/test/widgets/mesh_link_painter_test.dart` (new)

Produces (consumed by Task 5):
- `MeshLayout.compute(Map<String, NodeStatus> nodes, Map<String, Offset> pinned) → Map<String, Offset>`
  (normalized 0–1; spec §Layout engine rules: rank rows at
  `y = 0.12 + row*0.18`, stable zone-else-id ordering, pinned override,
  unknown-rank → unplaced bottom row, unknown-parent safe).
- `LinkQuality.of(int? rssi, bool online) → (Color, bool dashed)` per the
  spec table (thresholds −60/−75; offline/null ⇒ grey + dashed), colors
  via `AppColors`.
- `MeshLinkPainter extends CustomPainter` — draws child→parent lines from
  positions, quality colors, dashed for stale, animated dash-offset flow
  (`double phase` ctor param, 0–1), midpoint `-61 dBm` label above a zoom
  threshold, quadratic slight curve so overlapping links stay readable.
- `MeshNodeCard` — spec §Node card: router/sensors icon, online color,
  zone-else-short-MAC title, battery chip (helper extracted from
  `NodeListTile._batteryIcon` into
  `app/lib/screens/devices/battery_icon.dart` and reused by both), RSSI
  chip, moon badge when sleepy, greyscale+pill when offline; `onTap`
  callback (detail sheet is Task 5's).

- [ ] **Step 1 (tests first):** layout matrix (bridge alone; 3-node chain;
  two rank-1 siblings stable order; pinned node excluded from spreading;
  unknown parent doesn't throw; unknown rank → unplaced row) and
  `LinkQuality` table matrix.
- [ ] **Step 2:** implement the three files + helper extraction
  (`node_list_tile.dart` updated to use it; its existing widget test still
  green).
- [ ] **Step 3:** `flutter test` green; commit —
  `feat: mesh map layout engine, link painter, node card`

---

### Task 5: The screen — assembly, animation, drag-to-pin

**Files:** `mesh_map_screen.dart`, `node_positions_store.dart`, test
`app/test/widgets/mesh_map_screen_test.dart` (new)

- [ ] **Step 1 (tests first):** pump `MeshMapScreen` with an overridden
  `nodesProvider` (pattern: existing screen tests, e.g.
  `history_screen_test.dart`): cards render zone/battery/RSSI/moon; bridge
  card present; offline node shows pill; degraded-data banner appears when
  nodes exist but none has mesh info; empty state ("No nodes detected
  yet") when map is empty. Store test: pin → reload → position survives
  (use `SharedPreferences.setMockInitialValues`).
- [ ] **Step 2:** `node_positions_store.dart` per spec (key
  `mesh_map_positions_v1`, normalized JSON, `load/pin/unpin`), exposed as
  a Riverpod `FutureProvider`/`Notifier`.
- [ ] **Step 3:** screen assembly: `Scaffold(appBar: 'Mesh Map')` →
  `InteractiveViewer(minScale .5, maxScale 3)` → fixed 1200×1600 `Stack`:
  `CustomPaint(MeshLinkPainter)` under `AnimatedPositioned` cards (250 ms);
  one repeating `AnimationController` (1.5 s) drives the painter's `phase`;
  card drag via `onPanUpdate` (live position) + `onPanEnd` (pin);
  long-press → unpin confirm dialog; tap → detail bottom sheet (MAC, rank,
  parent, RSSI, battery mV/%, last seen, zone).
- [ ] **Step 4:** `flutter test` green; manual run against the Task 1
  simulator: links re-attach when `node3` flips parent; drag a card, kill
  and relaunch the app, position held.
- [ ] **Step 5:** Commit — `feat: live mesh map screen with drag-to-pin`

---

### Task 6: Entry point + docs

- [ ] **Step 1 (test first):** extend
  `app/test/widgets/...devices` coverage: Devices `AppBar` has a hub icon;
  tapping navigates to `MeshMapScreen`.
- [ ] **Step 2:** add the `IconButton(Icons.hub)` action to
  `devices_screen.dart` pushing `MaterialPageRoute(MeshMapScreen())`.
- [ ] **Step 3:** docs sync — `docs/technical/13-mobile-app.md` (new
  screen), `docs/technical/05-mqtt-broker.md` (`/mesh` topic if the
  firmware plan's Task 5 hasn't already), `TODO.md` §App feature gaps
  (devices-screen UX pass: note the map shipped).
- [ ] **Step 4:** full `flutter test` + `flutter analyze` clean (CI
  parity); commit — `feat: mesh map entry point from devices screen + docs`

---

## Sequencing note for the coordinator

Task 1 is independent (Python). Tasks 2→3→4→5→6 are ordered by interface
dependency, but Task 4 only needs Task 2's model (not Task 3), so 3 and 4
can run in parallel after 2. Nothing here waits on the firmware plan
(`2026-07-26-mesh-deep-sleep.md`) — the two plans meet only at the MQTT
contract; if any contract change becomes necessary, update both specs and
both implementations in the same change.
