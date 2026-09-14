# Unlimited-Sensors Onboarding — Bench-Testing Bugfixes

**Goal:** Fix the reliability/UX gaps found while actually enrolling zone2/3/4
through the app for the first time (2026-09-12 bench session). The crypto and
happy-path enrollment flow already work end-to-end (proven live on zone2,
zone4) — these are the rough edges that made getting there needlessly painful
and that would bite a real customer.

**Context:** `docs/superpowers/plans/2026-09-09-unlimited-sensors-app-onboarding.md`
(the feature this hardens). Full incident narrative in this session's chat —
short version below per task.

---

### Task 1: Bridge NetKey does not survive a bridge reset — **DONE**

**Root cause:** The bridge holds its NetKey in RAM only, by design (never
written to bridge flash). `serial_bridge.py::run()` sent the NetKey to the
bridge exactly once, right after the Python process starts. If the bridge
chip reset independently (reflash, brown-out, crash) while `serial_bridge.py`
kept running, the bridge came back up with `bridgeHasNetKey = false` and
never re-requested it — so it never sent its own rank-0 beacon again, and no
sensor could find a route until a human ran
`sudo systemctl restart greenhouse-serial-bridge`. Hit for real today after
reflashing the bridge; cost ~20 minutes of "why is zone2 unrouted" before the
cause was found.

**Fix implemented:** `bridge_esp32.ino` sends
`{"type":"hello","mac":"..."}` once in `setup()`, right after `Serial1.begin`
and before `esp_now_init()`. `serial_bridge.py` gained `_handle_hello()`,
wired into `handle_line()`'s dispatch, which re-sends the cached NetKey on
receiving it. `handle_line()` now takes the serial connection so the handler
can write back to it.

**Files changed:** `firmware/bridge_esp32/bridge_esp32.ino`,
`pi/scripts/serial_bridge.py`.

- [x] Bridge emits `hello` on boot
- [x] `serial_bridge.py` resends NetKey on receiving `hello`
- [x] Bench-verified live 2026-09-12: reflashed the bridge (Pi kept running),
      `journalctl -u greenhouse-serial-bridge` showed
      `[serial-bridge] netkey re-sent on hello from bridge` immediately on
      the bridge's fresh boot — no manual `systemctl restart` needed.

**Note:** the physical bridge board is an **ESP32-C3** (same silicon as the
edge nodes, confirmed via `esptool`'s chip detection), not a classic ESP32,
despite the `firmware/bridge_esp32/` folder name. Flash it with
`--fqbn esp32:esp32:esp32c3`, not `esp32:esp32:esp32` (the latter compiles
fine but fails at upload with "This chip is ESP32-C3, not ESP32").

---

### Task 2: A board's stale NVS AppKey silently defeats re-enrollment

**Status:** documentation-only, not yet written up in the actual runbook
(`INSTRUCTIONS.md` / `docs/DEVICES.md`) — only tracked here so far.

**Root cause:** `meshStoreSetAppKey(NODE_APP_KEY)` in every edge sketch's
`setup()` is guarded by `if (!meshStoreAppKey(existingAppKey))` — it only
seeds NVS when nothing is stored yet, so a board's compiled-in `node_key.h` is
silently ignored if *anything* was ever flashed to it before. The board still
boots, still beacons "unenrolled" normally, still receives the Pi's
provisioning blob over the air — it just can never decrypt it
(`meshOpenProvision` → `"provision blob rejected — not for us"`), forever,
with zero signal to the app or the Pi that the cause is a key mismatch rather
than a routing/range problem. Hit twice today (zone2, zone4); both needed
`esptool erase-flash` before a reflash actually took effect.

**Fix:** add "always erase-flash before a board's first enrollment" to
wherever the enrollment steps actually get read
(`pi/tools/provision_sensor.py`'s own printed instructions, and/or
`docs/DEVICES.md`). Arduino/`arduino-cli` project — use
`esptool --chip esp32c3 -p <PORT> erase-flash` (this repo does not use
ESP-IDF/`idf.py`).

- [ ] Add the erase-flash reminder to `provision_sensor.py`'s printed output
- [ ] Mention it in `docs/DEVICES.md`'s enrollment section

---

### Task 3: Ghost retained MQTT topics on sensor removal — **DONE (partial)**

**Root cause:** `pi/scripts/clear_retained.sh` already existed for manual
cleanup (written after a 2026-08-12 incident), but nothing called it
automatically. Every zone name a sensor was ever enrolled under left its
`greenhouse/<zone>/sensors/<metric>` retained topics on the broker forever,
and separately each removed node's own `greenhouse/nodes/<mac>/{status,
battery,mesh}` topics also lingered. The Dashboard renders one tile per
distinct zone key it has ever seen retained, so this showed up as phantom
zones. Today's bench session alone produced 3 ghost zones before a manual
`clear_retained.sh` run (see [[app-bugs-discovered]]).

**Fix implemented:** `portal.py` gained `clear_node_retained(mac)`, called
from `api_nodes_delete()` after a successful removal. It publishes an empty
retained payload to that MAC's `status`/`battery`/`mesh` topics on the
**local** broker only.

**Deliberately NOT covered by this fix** (scope note, not a bug):
zone-level reading topics (`greenhouse/<zone>/sensors/<metric>`) are left
alone, since a zone can in principle be shared by more than one node and it's
not safe to wipe a shared metric topic when only one node using it is
removed — `clear_retained.sh` remains the tool for a full zone retirement.
The **cloud (HiveMQ) side** is also not touched by this fix — same two-broker
caveat `clear_retained.sh`'s own header documents (a local retained-delete
crosses the bridge as a non-retained message, so the cloud copy survives) —
an operator should still run `clear_retained.sh` for the MAC if a HiveMQ
bridge is configured and the ghost must be gone from there too.

**Files changed:** `pi/portal/portal.py`.

- [x] `api_nodes_delete()` clears the removed node's own retained topics
      (local broker)
- [ ] Bench-verify: remove a sensor from the app, confirm its `status`/
      `battery` topics are gone (`mosquitto_sub --retained-only`) — **not yet
      done live, code review only so far**
- [ ] Decide later, only if it becomes a real annoyance again: whether
      zone-level reading topics also need an automatic path (rename support
      doesn't exist yet in the app, so this hasn't come up for real)

---

### Task 4: Devices list transiently empty after app force-close/reopen

**Status:** unchanged — still needs reproduction before any fix, not touched
by today's follow-up work.

**Root cause:** not confirmed — observed once (zone4 missing from Devices
right after a cold app relaunch, present again after the Pi was
power-cycled), not yet reproduced deliberately. Working theory: a timing
race in the very first burst of retained-topic redelivery on MQTT reconnect
(`mqtt_connection.dart` subscribes `greenhouse/#`; `nodes_provider.dart`'s
stream starts from an empty map and fills in as retained messages arrive).
If real, this would self-heal within a second or two rather than needing a
full Pi restart, which doesn't match what was observed — so the theory as
stated is probably incomplete.

- [ ] Reproduce deliberately: force-close the app while a sensor is enrolled
      and online, reopen, and watch whether it's *actually* missing or just
      slow to reappear (a few seconds)
- [ ] If reproducible, add temporary logging around the MQTT reconnect +
      `nodesProvider` merge to see what's actually arriving and when
- [ ] Only then decide on a fix

---

### Already fixed this session (no action needed)

- `firmware/fake_edge_node_esp32_c3.ino` was still on the pre-v2 protocol
  (no provisioning gate, old `MESH_MAGIC`, stale `meshRtcRestore()` ordering
  bug) — brought in line with `edge_node_esp32_c3.ino`. Bench-verified live:
  zone2 and zone4 both enrolled and reported real (fake) readings through
  the full mesh → bridge → Pi → MQTT path.

### Task 4.5: Real edge nodes never published `/mesh` — Mesh Map always showed them as unplaced/disconnected — **DONE**

**Root cause:** `pi/tools/simulator.py::build_mesh_payload()` publishes a
retained `greenhouse/nodes/<mac>/mesh` record (parent/rank/rssi/sleepy/
battery_mv/zone) for every simulated node — that's what the Mesh Map screen
(`app/lib/screens/devices/mesh_map_screen.dart` → `mesh_layout.dart`) was
built and tested against. But the real production path,
`serial_bridge.py::handle_frame()` (processing an actual decrypted v2
sensor packet from real hardware), never published this topic for edge
nodes — only `_handle_mesh()` did, and that's only ever called for the
bridge's own boot-time self-record. Every real sensor therefore always had
`meshRank == null`, which `MeshLayout.compute()` sends straight to the
bottom "unplaced" row with no link line — regardless of actual mesh routing
health. Confirmed live today: zone2/zone4 were genuinely connected and
delivering readings (`[mesh] parent=... rank 1` in their own serial log)
while the app showed them as disconnected.

**Fix implemented:** `handle_frame()` now also calls `_publish_mesh()` with
`parent` (`body.parent_mac` hex, `None` if all-zero), `rank` (`header.rank`),
`rssi` (`body.parent_rssi`), `sleepy` (`node.sleepy`), `battery_mv`
(`body.battery_mv or None`), `zone` (`node.zone`) — same shape the simulator
already used, so no app-side change needed.

**Files changed:** `pi/scripts/serial_bridge.py`.

- [x] `handle_frame()` publishes a real `/mesh` record per edge node
- [x] Bench-verified live: after deploying + restarting
      `greenhouse-serial-bridge`, zone2 and zone4 both showed up in
      `greenhouse/nodes/<mac>/mesh` with correct parent/rank/rssi, and the
      Mesh Map screen changed from showing them unplaced to
      "3 sensors · 3 working · 0 not responding" with real "Direct"
      connection cards.

---

### Task 4.6: `handle_frame()` published real sensor readings to a topic nothing consumes — **DONE**

**Status:** fixed and deployed; live bench-verification in progress (this is
the single biggest bug found this session — likely present since the v2
mesh/crypto feature was first implemented, not something introduced today).

**Root cause:** `serial_bridge.py::handle_frame()` (the v2 encrypted-packet
path — what every real mesh sensor actually uses) published readings to
`greenhouse/<zone>/sensors/<temperature|humidity|soil_moisture>`. But
**every real consumer expects a different, metric-specific topic shape**:
- `pi/scripts/recorder.py`'s `SUBSCRIBE_TOPICS` (history/charts):
  `greenhouse/+/air/temperature`, `greenhouse/+/air/humidity`,
  `greenhouse/+/soil/moisture`.
- The app's `SensorReading.fromMqtt` (`app/lib/models/sensor_reading.dart`)
  parses the metric as everything after `greenhouse/<zone>/`, i.e. it's
  *designed* to read compound keys like `air/temperature` — which
  `zone_card.dart` then looks up verbatim (`readings['air/temperature']`,
  `readings['soil/moisture']`).
- `pi/scripts/weather.py`'s legacy `_handle_reading()` (the older plain-UART
  `{"type":"reading","zone":...,"group":"air","metric":"temperature",...}`
  path, still covered by `pi/tests/test_serial_bridge.py`) already published
  correctly to this same `air`/`soil` shape — it's the *contract* every
  other piece of the system was built against.

`handle_frame()`'s `'sensors'` group literal matched **none** of them. Real
sensor readings reached the Pi, decrypted successfully, and were retained
on a topic nobody ever subscribed to. Net effect: the Dashboard always
showed zones with no metric chips (just the name + leaf icon, "Waiting for
sensor data…" until at least one zone existed) and the history recorder
never stored a single real mesh-sourced data point — both invisible failures
with no error anywhere, since MQTT publish/retain always "succeeds"
regardless of whether anyone's listening. Caught only because today was the
first time a real v2-enrolled sensor's Dashboard tile was actually looked at
closely (`git log` shows no test ever asserted `handle_frame`'s exact
published topic string — `test_serial_bridge.py` only covers the legacy
`_handle_reading` path).

**Fix implemented:** `_METRICS` is now a `{metric: (group, topic_metric)}`
map (`temperature`→`('air','temperature')`, `humidity`→`('air','humidity')`,
`soil_moisture`→`('soil','moisture')`) and `handle_frame()`'s publish loop
uses it, so a v2 sensor's readings land on exactly the topics
`recorder.py`/the app already expect.

**Files changed:** `pi/scripts/serial_bridge.py`.

- [x] `handle_frame()` publishes to `air/temperature`, `air/humidity`,
      `soil/moisture` instead of `sensors/<metric>`
- [x] `pi/tests/` full suite green (243/244, same pre-existing unrelated
      Windows failure)
- [ ] Bench-verify live: confirm zone2/zone4's next wake publishes to the
      corrected topics, then confirm the Dashboard's ZoneCard chips actually
      render values (not just the empty name+icon tile) — in progress as
      this is written
- [ ] Separately worth doing later: the old `greenhouse/<zone>/sensors/<metric>`
      topics are now stale retained garbage on the broker — not urgent, but
      `clear_retained.sh 'greenhouse/+/sensors/#'` would clean them up
- [ ] Consider adding the test `test_serial_bridge.py` never had: assert
      `handle_frame()`'s exact published topics for a decrypted packet, so
      this specific class of regression can't silently reappear

---

### Task 5: App never re-probes LAN once it falls back to remote

**Status:** observed, not fixed — low priority.

**Root cause:** `mqtt_connection.dart::_attempt()` remembers whether the last
successful connection was `'local'` or `'remote'` (SharedPreferences) and
tries that one first on every reconnect — but it only tries the *other* host
if the preferred one's `_tryConnect` actually fails. Once a connection falls
back to remote (e.g. a one-off timing miss on the very first attempt right
after pairing — reproduced once, then a fresh pairing attempt connected to
LAN fine on the first try, so this looks like an occasional transient rather
than a persistent LAN fault), it will keep using remote indefinitely even
after LAN becomes reachable again, since remote keeps succeeding and nothing
ever re-tries local.

**Possible fix (not implemented):** periodically re-attempt LAN in the
background even while connected via remote (e.g. every N minutes), and
switch back if it succeeds — rather than only re-evaluating on a full
disconnect/reconnect cycle. Low priority: cosmetic ("Connected from away"
banner shown while on the home network) rather than a functional break,
data still flows correctly either way.

---

### Task 6: A DELETE test against live Pi data wiped the entire node trust store

**Status:** recovered, process lesson only — no code defect.

**What happened:** at some point during today's session, `/etc/greenhouse/nodes.json`
was found reduced to `{"version": 1, "nodes": []}` — all three enrolled
sensors (zone2, zone3, zone4) gone from the Pi's trust store. Timing
(file mtime 13:12:37) lines up with the window another agent was bench-testing
Task 3's `DELETE /api/nodes/<mac>` retained-topic cleanup — the most likely
explanation is that verification was done by calling `DELETE` against the
real Pi with real MACs instead of a disposable/test fixture. No direct log
proof survives (the Pi rebooted at 14:40 and journald here is non-persistent
across boots), so this is circumstantial, not confirmed.

**Recovery:** none of the physical boards needed re-flashing or
re-provisioning — `nodes.json` is a Pi-side trust store only; each board's
own AppKey/NetKey/provisioned state lives in its own NVS and was untouched.
Re-added all three via `POST /api/nodes` with their already-known real
MAC/key pairs (recorded earlier in this same session's notes) and clean
zone names (`zone2`/`zone3`/`zone4`, replacing the throwaway test names like
`q`/`1`/`;` from earlier bench attempts). They resumed reporting on their
next sleepy wake with no firmware changes needed.

**Lesson for next time:** verifying a destructive endpoint (delete/remove)
against a live device fleet — even "just to check the log line appears" —
risks exactly this. Prefer testing against a disposable MAC that isn't a
real enrolled sensor, or explicitly warn/confirm before calling a real
DELETE during bench verification.

---

## How to apply

Tasks 1 and 3 have code written and reviewed (matches project conventions,
`pi/tests/` suite green apart from one pre-existing Windows-only
file-permission test unrelated to this work — 243/244 passing, confirmed
against `main` before these changes too). **Neither has been bench-verified
live yet** — that's the next step before considering them fully done. Task 2
is a docs-only change, not yet written. Task 4 needs evidence before it needs
code.
