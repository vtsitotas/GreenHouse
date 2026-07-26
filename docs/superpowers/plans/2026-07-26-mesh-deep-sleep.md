# Mesh Deep Sleep (Phase 1: Leaf Sleep) + Telemetry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Battery-designated ("sleepy") edge nodes deep-sleep 15 minutes
between readings while the mesh keeps working around them: sleepy nodes are
leaf-only (their beacons carry a sleepy flag and are never adopted as
parents), always-on nodes and the bridge carry all relay duty. Every data
packet additionally carries battery millivolts, the origin's parent MAC, and
parent RSSI; the bridge republishes battery % and a topology JSON to MQTT
(retained) for the visualization feature.

**Architecture:** One wire-format bump (beacon 18→19 B, data packet
23→33 B, fields appended, fleet reflashed together). `TRUSTED_NODES[]`
gains a `sleepy` role flag — one edge firmware image serves both roles by
looking up its own MAC. Sleepy nodes replace the always-on `loop()` with a
single-pass wake cycle in `setup()` (restore RTC state → sensor warm-up
concurrent with radio bring-up and one flagged beacon → read → send/buffer →
persist RTC state → `esp_deep_sleep`). Seq counters, parent hint, WiFi
channel, and the 10-reading buffer persist in RTC slow memory. Bridge gets
per-role offline windows, publishes `greenhouse/nodes/<MAC>/battery` and
`/mesh`, its own root `/mesh` record, and an MQTT LWT for its own status.

**Tech Stack:** C++ / Arduino (ESP32 Arduino Core v3.x), ESP-NOW, deep sleep
(`esp_sleep.h`, `RTC_DATA_ATTR`), PubSubClient (bridge). No Pi or app code
in this plan (the app half lives in `2026-07-26-mesh-field-visualization.md`).

**Reference spec:** `docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md`

> **⚠ TDD does not apply to this plan** — Arduino firmware, no automated
> harness (same rationale as `2026-07-09-dynamic-mesh-relay.md`). Per-task
> verification = Arduino IDE Verify on **both** boards (ESP32C3 Dev Module
> and ESP32 Dev Module); end-to-end = the bench checklist in Task 6.

## Global Constraints

- Wire format: append-only growth, exactly per the spec's §Wire format —
  `MeshBeacon` 19 B, `MeshDataPacket` 33 B. Length remains the message-type
  discriminator; never let the two sizes collide.
- Not backward compatible with the 18/23-byte fleet: all three device types
  reflash together in Task 6, no mixed operation.
- Tuning constants exactly as spec §Config: `MESH_FLAG_SLEEPY 0x01`,
  `MESH_SLEEP_INTERVAL_MS 900000`, `MESH_WAKE_DISCOVERY_MS 5000`,
  `MESH_TX_CONFIRM_WAIT_MS 500`, `MESH_WAKE_MAX_AWAKE_MS 10000`,
  `MESH_MIN_SLEEP_MS 1000`. Bench (Task 6) may tune afterward.
- Sleepy behavior is driven **only** by the node's own entry in
  `TRUSTED_NODES[]` — no per-sketch `#define`s, no divergent images.
- Existing routing invariants untouched: strict-rank rule, RSSI tiebreak,
  trickle timing, TTL/dedup, orphan beacon, TX-failure backstop. The only
  new routing rule: a sleepy-flagged beacon is never a parent candidate.
- Always-on edge nodes keep today's loop behavior byte-for-byte except the
  new telemetry fields in outgoing packets.
- `SensorPacket` stays byte-identical (temperature, humidity,
  soil_moisture); telemetry fields live in `MeshDataPacket`, after it.
- Battery→percent mapping lives on the bridge only (single tunable place);
  nodes send raw millivolts (`0` = not measured).
- MQTT contract exactly per spec §Telemetry (the visualization plan builds
  against it — if you must deviate, update BOTH specs and the sim in the
  other plan's Task 1).
- No changes under `app/` or `pi/` in this plan.

## File Structure

| File | Change |
|---|---|
| `firmware/libraries/GreenhouseMesh/mesh_config.h` | Constants, `TrustedNode.sleepy`, updated `TRUSTED_NODES[]` |
| `firmware/libraries/GreenhouseMesh/mesh_node.h` | Struct growth, sleepy-beacon rule, telemetry capture, `meshIsSelfSleepy()`, RTC state save/restore helpers, beacon flags plumb-through |
| `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` | Battery ADC read; sleepy wake-cycle path in `setup()`; always-on path unchanged otherwise |
| `firmware/edge_node_esp32/edge_node_esp32.ino` | Same as C3 (its own pins) |
| `firmware/bridge_esp32/bridge_esp32.ino` | Per-role offline windows, `/battery` + `/mesh` publishing, own root record + LWT |

---

### Task 1: Config + wire format + sleepy routing rule (`GreenhouseMesh` library)

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_config.h`
- Modify: `firmware/libraries/GreenhouseMesh/mesh_node.h`

**Interfaces produced (used by Tasks 2–5):**
- Constants + `MESH_FLAG_SLEEPY`; `TrustedNode` with `bool sleepy`;
  `TRUSTED_NODES[]` marking the C3 edge node (`zone1`) sleepy `true`, the
  WROOM (`zone2`) `false`, bridge `false`.
- `MeshBeacon` (19 B) with trailing `uint8_t flags`; `MeshDataPacket`
  (33 B) with trailing `battery_mv, parent_mac[6], parent_rssi, flags`.
- `bool meshIsSelfSleepy()` — self-MAC lookup in `TRUSTED_NODES[]`.
- `void meshSetBatteryMv(uint16_t mv)` — sketch injects the measured value;
  `meshSendReading()` stamps it plus `parent_mac` (current parent's MAC or
  zeros), `parent_rssi` (`meshParentRssi`, clamped to int8, −128 = none),
  and the sleepy flag into every outgoing/buffered packet.
- `meshSendBeaconNow()` sets `flags = meshIsSelfSleepy() ? MESH_FLAG_SLEEPY : 0`.

- [ ] **Step 1: `mesh_config.h`** — add the §Config constants block; add
  `bool sleepy` to `TrustedNode`; extend all three `TRUSTED_NODES[]`
  initializers (`{ {..mac..}, "zone1", true }` etc. — bridge `false`).
- [ ] **Step 2: `mesh_node.h` structs** — append the new fields exactly as
  spec §Wire format (comments included); add
  `static uint16_t meshBatteryMv = 0;` and `meshSetBatteryMv()`.
- [ ] **Step 3: Sleepy rule in `meshHandleBeacon()`** — after the trusted
  check and neighbor-liveness update (currently `mesh_node.h:202-203`) and
  **before** the current-parent branch, add:

```c
  // Sleepy nodes are leaf-only: never adopt one as a parent. Their beacons
  // still refresh neighbor liveness above. If our CURRENT parent starts
  // advertising sleepy (role change + reflash), drop it like a rank loss.
  if (b->flags & MESH_FLAG_SLEEPY) {
    if (idx == meshParentIdx) meshDropParent("parent became sleepy");
    return;
  }
```

- [ ] **Step 4: Telemetry stamping in `meshSendReading()`** — populate
  `pkt.battery_mv = meshBatteryMv;`, `pkt.parent_mac` (parent's MAC when
  routed, zeros when unrouted — `meshFlushBuffer()` re-sends buffered
  packets unmodified, same policy as the existing TTL note),
  `pkt.parent_rssi`, `pkt.flags`. `meshRelayData()` must NOT touch the new
  fields (origin-owned, like `origin_mac`).
- [ ] **Step 5: Verify** — scratch sketch printing
  `sizeof(MeshBeacon)`/`sizeof(MeshDataPacket)`; expect **19 / 33** on both
  boards (Verify only; flash optional).
- [ ] **Step 6: Commit** — `feat: mesh wire-format v2 — sleepy role flag + battery/parent telemetry`

---

### Task 2: RTC-persistent mesh state (`mesh_node.h`)

**Interfaces produced (used by Task 3):**

```c
typedef struct {
  uint32_t       magic;        // MESH_RTC_MAGIC 0x47534C50 ('GSLP')
  uint16_t       dataSeq, beaconSeq;
  int8_t         parentIdx;    // hint only; -1 = none
  uint8_t        parentRank;
  uint8_t        channel;      // last known WiFi channel
  uint8_t        bufCount, bufHead;
  MeshDataPacket buf[MESH_DATA_BUFFER_SIZE];
} MeshRtcState;
// RTC_DATA_ATTR instance lives in mesh_node.h (single translation unit per sketch)

bool meshRtcRestore();  // valid magic AND wakeup cause == timer → load seq
                        // counters/buffer/channel + parent hint into the
                        // static state, return true. Else zero + return false.
void meshRtcPersist();  // snapshot statics back into RTC memory (call last
                        // thing before esp_deep_sleep_start()).
```

- [ ] **Step 1:** Implement struct + both functions. Restoring the parent
  hint sets `meshParentIdx/meshParentRank/meshMyRank = rank+1` and
  `meshParentLastHeardMs = millis()` so the parent-timeout check doesn't
  instantly fire, but the *hint-not-trust* contract holds: the Task 3 wake
  flow confirms via TX result or fresh beacon.
- [ ] **Step 2:** Guard: `esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER`
  (power-on, flash, brownout) ⇒ treat RTC state as invalid even with good
  magic, **except** keep `dataSeq` if magic is valid (a manual reset
  mustn't re-collide seq with the bridge's dedup cache).
- [ ] **Step 3:** Verify both boards compile; commit —
  `feat: RTC-persistent mesh state for deep-sleep wake cycles`

---

### Task 3: Sleepy wake cycle in both edge sketches

**Files:** both edge `.ino` files (C3 shown; WROOM identical logic, its own
pins — battery ADC pin per Step 1).

**Behavior contract (spec §The sleepy wake cycle, numbered steps 1–11):**
`meshIsSelfSleepy()` false ⇒ today's `setup()`/`loop()` runs unchanged.
True ⇒ `setup()` runs the single-pass cycle and never returns to `loop()`.

- [ ] **Step 1: Battery ADC** — pick a free ADC1 pin per variant (C3:
  GPIO3/ADC1_CH3; WROOM: GPIO35, input-only) with a 2×220 kΩ divider
  documented in a header comment. `readBatteryMv()`: 8-sample average of
  `analogReadMilliVolts()` × 2. Call `meshSetBatteryMv()` from **both**
  roles (always-on nodes report too when the divider is fitted; an
  unfitted pin floating near 0 naturally reads as "not measured" — clamp
  readings < 2000 mV to 0).
- [ ] **Step 2: Wake-cycle function** `runSleepyCycle()` implementing spec
  steps 1–11 with a `millis()` deadline loop instead of `delay()`s:
  restore RTC (Task 2) → sensors ON → radio up on restored channel (fall
  back to full `getWiFiChannel()` scan when restore failed) → `meshInit(0)`
  → one `meshSendBeaconNow(meshMyRank, MESH_SLEEP_INTERVAL_MS)` (its
  advertised interval tells neighbors how long until we're heard again) →
  pump until warm-up elapses (recv callback runs as today) → read sensors,
  sensors OFF, `meshSetBatteryMv(readBatteryMv())` → `meshSendReading()`
  → wait ≤ `MESH_TX_CONFIRM_WAIT_MS` for the send callback; on failure the
  existing 3-fail backstop plus an explicit orphan path: listen up to
  `MESH_WAKE_DISCOVERY_MS`, retry once → `meshRtcPersist()` →
  `esp_sleep_enable_timer_wakeup((MESH_SLEEP_INTERVAL_MS − millis()) clamped
  to ≥ MESH_MIN_SLEEP_MS)` → `esp_deep_sleep_start()`. Whole function
  bounded by `MESH_WAKE_MAX_AWAKE_MS` (check the deadline in every wait
  loop; on breach: persist + sleep immediately).
- [ ] **Step 3: Wire into `setup()`** — after the existing ESP-NOW/mesh
  init block: `if (meshIsSelfSleepy()) runSleepyCycle();  // never returns`.
  Skip the 1500 ms USB-CDC wait when the wake cause is the timer (it's
  1500 ms of battery per cycle spent waiting for a serial monitor that
  isn't attached in the field); keep it on power-on wakes for bench work.
- [ ] **Step 4:** Apply identically to the WROOM sketch.
- [ ] **Step 5:** Verify both boards compile; commit —
  `feat: leaf deep-sleep wake cycle for sleepy edge nodes + battery sensing`

---

### Task 4: Bridge — per-role liveness + telemetry publishing

**Files:** `firmware/bridge_esp32/bridge_esp32.ino`

- [ ] **Step 1: Per-role offline windows** — in `checkOfflineNodes()`
  (`bridge_esp32.ino:173-190`) replace the global window with
  `expectedMs(i) = TRUSTED_NODES[i].sleepy ? MESH_SLEEP_INTERVAL_MS
  : MESH_EXPECTED_REPORT_INTERVAL_MS`, threshold `MESH_OFFLINE_AFTER ×
  expectedMs(i)`.
- [ ] **Step 2: Battery %** — `batteryPctFromMv(uint16_t mv)`: LiFePO4
  piecewise-linear table
  `{3400:100, 3350:90, 3320:80, 3300:70, 3280:60, 3260:50, 3250:40, 3220:30, 3200:20, 3000:10, 2800:0}`
  with linear interpolation between entries, clamped. In `onDataRecv()`,
  when `pkt.battery_mv > 0`: publish `%.1f` to
  `greenhouse/nodes/<MAC>/battery`, retained.
- [ ] **Step 3: `/mesh` JSON** — in `onDataRecv()` after the existing
  publishes, build (snprintf, 192-byte buffer) and publish retained to
  `greenhouse/nodes/<MAC>/mesh` exactly per contract:
  `parent` = 12-hex of `pkt.parent_mac` (JSON `null` when all-zeros),
  `rank` = `origin_rank`, `rssi` = `parent_rssi` (`null` when −128),
  `sleepy` = flags bit0, `battery_mv` (`null` when 0), `zone`. Omit `ts`
  (bridge has no RTC epoch; field is optional in the contract).
- [ ] **Step 4: Bridge's own records** — at MQTT (re)connect: publish own
  `/mesh` (`{"parent":null,"rank":0,"rssi":null,"sleepy":false,"battery_mv":null,"zone":null}`)
  and own `/status` `online`, both retained; register LWT
  (`connect(id, user, pass, willTopic=<own /status>, willQoS 1,
  willRetain true, willMessage "offline")`) in **both**
  `reconnectMQTT()` and `reconnectMQTTNonBlocking()`.
- [ ] **Step 5:** Verify (ESP32C3 Dev Module — the bridge is a C3);
  commit — `feat: bridge publishes battery + mesh topology, per-role offline windows`

---

### Task 5: Documentation sync

- [ ] Update `docs/technical/03-mesh-routing.md` (wire structs §1, new
  sleepy rule §3) and `docs/technical/05-mqtt-broker.md` (the `/battery`
  note "μόνο simulator σήμερα" is no longer true; add `/mesh`).
- [ ] `TODO.md`: move the deep-sleep items — §2 clock-sync entry gets a
  pointer to the spec's Phase 2 section; §4 "zero deep-sleep code exists"
  is stale after this plan; add "Phase 2 synchronized wake windows" as the
  explicit follow-on.
- [ ] `docs/EDGE_NODE_POWER_OPTIMIZATION.md`: mark §4's conceptual flow as
  implemented-by this plan, link the spec.
- [ ] Commit — `docs: sync mesh/MQTT/TODO docs with deep-sleep phase 1`

---

### Task 6: Bench validation (physical hardware — cannot run in the dev sandbox)

Reflash all three devices (bridge, C3 edge = sleepy, WROOM edge =
always-on), then walk the spec §Testing checklist verbatim:

- [ ] 19/33-byte struct sizes in serial logs on all devices.
- [ ] Sleepy C3: full wake cycle ≤ 3.5 s on the healthy path; measured
  sleep interval ≈ 15 min; `dataSeq` strictly increasing across ≥ 3 cycles
  with **zero** dedup drops in the bridge log.
- [ ] Kill the sleepy node's parent mid-sleep → next wake re-parents or
  buffers, always sleeps again within `MESH_WAKE_MAX_AWAKE_MS`.
- [ ] `MESH_TEST_IGNORE_BRIDGE` on the WROOM: it must never adopt the
  sleepy C3 while the C3 is awake (sleepy beacons rejected).
- [ ] `mosquitto_sub -v -t 'greenhouse/nodes/#'`: retained `/battery` and
  `/mesh` per contract; bridge's own root record; pull the bridge's power
  → LWT `offline` appears.
- [ ] Offline timing: sleepy ≈ 45 min, always-on ≈ 15 s.
- [ ] Sleep-current measurement on the modified board (target ~55 µA; log
  the actual value into `docs/EDGE_NODE_POWER_OPTIMIZATION.md`).
- [ ] Record per-wake clock error (serial log wake timestamps vs expected)
  — this is the Phase 2 drift dataset; note results in `TODO.md`.
