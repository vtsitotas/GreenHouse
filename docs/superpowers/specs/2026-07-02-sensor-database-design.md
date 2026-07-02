# Greenhouse IoT — Sensor Database Design Spec (Slice 3, revised)

**Date:** 2026-07-02
**Author:** Bill
**Status:** Approved for implementation
**Slice:** 3 of 6 — Storage + History (supersedes the InfluxDB assumption in the Slice 1 spec's decomposition table)

---

## 1. Context & Scope

Today there is **no persistence of sensor or weather data anywhere in the system**. Mosquitto's "persistence" only means retained-message-per-topic (last value survives a broker restart, nothing else). `weather.py` only publishes. The Flutter app keeps history in in-memory Riverpod state, lost on every app/Pi restart. Automation rules in `/etc/greenhouse/rules.json` are evaluated **statelessly** against only the live weather reading — there is no way today to express a duration condition like "soil moisture below 30% for more than 1 hour."

This spec covers adding a local database on the Pi Zero W to:
1. Durably store zone sensor readings (temperature/humidity/soil moisture per zone) and weather readings, surviving reboots.
2. Give the Flutter app a history/chart data source.
3. Enable **stateful** (duration-based) automation rules.
4. Do all of this within the real constraints of a Raspberry Pi Zero W: single-core ARM1176JZF-S @ 1GHz (ARMv6), 512MB RAM, running entirely off a microSD card shared with every other service on the box.

### Out of scope for this slice
- Multi-hop sensor mesh / relay bridging for far-away nodes (separate design track, firmware-side).
- Syncing/exporting data to an external machine for ML/statistical analysis (see §9 Future Work).
- Auth on the new history HTTP endpoint.
- Remote (off-LAN) history access via the HiveMQ bridge.
- An automation-history/audit event log.

### Corrections to prior assumptions (found verifying code for this spec)
- **`WEATHER_INTERVAL` is 30 seconds, not 30 minutes.** `pi/systemd/greenhouse-weather.service` sets `Environment=WEATHER_INTERVAL=30`, overriding the 1800s default in `pi/scripts/weather.py`. This means ~2,880 Open-Meteo calls/day today and that rules are already evaluated every 30s. Doesn't block this design but is worth a deliberate decision outside this spec.
- **The bridge firmware does not publish retained.** `firmware/bridge_esp32/bridge_esp32.ino` calls `mqtt.publish(topic, payload)` — the client library defaults to `retained=false` (only `tools/simulator.py` publishes retained). This contradicts the Slice 1 spec's "retained required" assumption and means zone cards can show empty after a broker restart until the next 5s packet. Unrelated to this spec's persistence layer (the recorder below doesn't depend on retain flags for correctness) but worth fixing separately.
- **The app already publishes `greenhouse/rules/update` and `greenhouse/rules/get` with no Pi-side consumer** — `weather.py` has no handler for either. Cheap to fix as part of this slice since the new recorder service is the natural long-lived MQTT subscriber to own it (see §8, step 9); not required for the core DB work.

---

## 2. Approach Comparison

### Option A — SQLite (Python stdlib `sqlite3`), WAL mode, batched writes — **CHOSEN**
- **RAM:** in-process, no daemon. Whole recorder service (Python + paho + SQLite page cache) fits in ~20-30MB RSS.
- **CPU:** at ~1 msg/s ingest with one batched transaction/minute, cost is effectively zero on the ARM1176.
- **SD wear:** fully controllable by write batching (see §3) — steady-state physical writes in the tens of MB/day, single-digit GB/year. Negligible against any card's endurance.
- **Packaging on ARMv6 Trixie:** zero install for the DB itself (`sqlite3` is stdlib); only new dependency is `python3-paho-mqtt` from apt (Debian package built for Pi OS's armhf — no pip/wheel compilation needed).
- **Ops:** one file (`/var/lib/greenhouse/greenhouse.db`); backup = copy the file; inspectable with `sqlite3` CLI or `python3 -c`. Right complexity for a single-maintainer thesis project.

### Option B — InfluxDB — **rejected**
- **No binaries for this CPU.** InfluxDB 2.x ships only amd64/arm64 — no 32-bit ARM build, let alone ARMv6. InfluxDB 1.8 had armhf builds but is EOL and armhf proper targets ARMv7 anyway. Would mean compiling Go from source on a 1GHz single core. Non-starter.
- **Memory floor.** Idle Go runtime + TSM engine sits at 150-300MB+ RSS — 30-60% of total RAM on a box already running mosquitto, Flask, weather.py, NetworkManager, and avahi.
- **Wear behavior:** TSM compaction does periodic large rewrite bursts — worse for SD cards than a plateaued SQLite file.
- **Complexity:** its own auth, HTTP API, Flux query language, retention-policy machinery — a second system to administer for three query shapes.
- The Slice 1 spec's decomposition table listed InfluxDB for this slice; that predates confirming the Pi Zero W as the actual target and the multi-zone/rules work. Superseded by this spec.

### Option C — RRDtool — considered, rejected
Apt-installable, tiny, fixed-size files with built-in downsampling — elegant for retention. But: one rigid file per series (awkward as zones are added), no ad-hoc queries for duration-based rule evaluation ("all minute buckets in the last hour below X" is painful), unfamiliar toolchain, needs `rrdcached` to avoid its own per-update write pattern. SQLite gives the same outcome with one flexible query surface.

(Flat append-only CSV/JSONL was also considered and rejected: queries and rollups become manual file-scanning code — more code than SQLite, not less.)

**Decision: SQLite, single dedicated writer process, WAL, minute-bucket ingest, hourly rollups.**

---

## 3. Write Path

### Owner: new dedicated service, `greenhouse-recorder`

New `pi/scripts/recorder.py`, run by new `pi/systemd/greenhouse-recorder.service`. Not folded into `weather.py`:
- `weather.py` is a poll loop that shells out to `mosquitto_pub`/`mosquitto_sub` to stay dependency-free; sensor ingest needs a persistent MQTT *subscription* (paho callback loop). Merging lifecycles means an Open-Meteo outage or rules bug could take down the DB writer.
- Matches the existing one-service-per-concern pattern (portal / weather / ap / watchdog).
- SQLite wants exactly one writer process; making the recorder the sole writer keeps concurrency trivial (everyone else opens read-only).

Uses `paho-mqtt` via `apt-get install python3-paho-mqtt` (apt, not pip — matches `install.sh` convention). Trixie ships paho 2.x, so use the `CallbackAPIVersion` shim already used in `tools/simulator.py`.

### Subscriptions
- `greenhouse/+/air/temperature`, `greenhouse/+/air/humidity`, `greenhouse/+/soil/moisture` (and `+/light/lux` for forward-compat) — zone readings.
- `greenhouse/weather/temperature|humidity|wind_kmh|uv_index|rain_mm_1h` — weather scalars.
- Skip messages with `msg.retain == True` in `on_message` — prevents re-recording stale values on every recorder restart, regardless of which publishers set retain.

### SD-wear protection (concrete mechanics)
1. **RAM buffering in 1-minute buckets.** Never write individual messages. Accumulate readings in a dict keyed by `(series, minute)`, tracking running `sum/min/max/count`. Edge nodes publish every 5s, but persisting one row per series per minute (avg/min/max/n) is a 12x row-count reduction before anything touches the SD card, with no meaningful loss for charts or rules.
2. **One transaction per flush interval.** Every `flush_seconds` (default 60), completed minute buckets are written via a single `executemany` inside one transaction — ~1,440 commits/day instead of ~86,000 synchronous writes/day in the naive per-message design.
3. **Pragmas** (set once at open):
   - `PRAGMA journal_mode=WAL` — sequential appends, readers never block the writer.
   - `PRAGMA synchronous=NORMAL` — fsync only at WAL checkpoints, not per commit. Power-loss risk is limited to the last few minutes of unflushed/uncheckpointed readings — the right tradeoff for telemetry.
   - Explicit `wal_checkpoint(TRUNCATE)` in the hourly maintenance pass to keep the WAL file bounded.
   - No `VACUUM` ever — with fixed retention the file plateaus and SQLite reuses freed pages in place, which is better for wear than periodic full-file rewrites.
4. **Graceful shutdown:** SIGTERM handler flushes the buffer before exit; unit sets `TimeoutStopSec=15`.
5. **Write-volume budget:** ~0.8MB/day of payload today → budget ~20-50MB/day physical with WAL/checkpoint amplification → under ~20GB/year written. A rounding error against any card's endurance.

---

## 4. Schema

```sql
CREATE TABLE series (
  id     INTEGER PRIMARY KEY,
  kind   TEXT NOT NULL,             -- 'zone' | 'weather'
  zone   TEXT,                      -- 'zone1'…; NULL for weather
  metric TEXT NOT NULL,             -- 'air_temperature','air_humidity','soil_moisture',
                                     -- 'temperature','humidity','wind_kmh','uv_index','rain_mm_1h'
  UNIQUE(kind, zone, metric)
);

CREATE TABLE readings (               -- minute resolution ("raw")
  series_id INTEGER NOT NULL REFERENCES series(id),
  ts        INTEGER NOT NULL,         -- unix seconds, start of minute bucket
  avg REAL NOT NULL, min REAL NOT NULL, max REAL NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)
) WITHOUT ROWID;                      -- clustered PK, ~40-50B/row all-in

CREATE TABLE readings_hourly (        -- identical shape, ts = start of hour
  series_id INTEGER NOT NULL REFERENCES series(id),
  ts INTEGER NOT NULL,
  avg REAL NOT NULL, min REAL NOT NULL, max REAL NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)
) WITHOUT ROWID;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
-- keys: 'schema_version', 'rollup_watermark' (last hour rolled up)
```

Integer `series_id` instead of repeating `zone`/`metric` text per row roughly halves row size and makes the range scan `(series_id, ts BETWEEN …)` a single b-tree walk — exactly what charts and rule evaluation need.

**Stateful rules need no extra table** — duration conditions are answered directly from `readings` (§5). Schema bootstrap (`CREATE TABLE IF NOT EXISTS…`) happens in the recorder itself on startup, keeping `install.sh` free of SQL.

---

## 5. Retention, Storage Sizing, and Read Path

### Retention policy

Defaults in `/etc/greenhouse/recorder.json` (all tunable):

| Tier | Resolution | Retention | Rows/day today (2 zones) | Rows/day @ 6 zones |
|---|---|---|---|---|
| `readings` | 1 minute (avg/min/max) | 90 days | 15,840 | 33,120 |
| `readings_hourly` | 1 hour | 730 days | 264 | 552 |

At ~50B/row effective (payload + b-tree overhead): raw plateaus at ~71MB (today) / ~150MB (6 zones) after 90 days; hourly adds ~10-20MB over 2 years. **Total footprint: ~80MB today, ~170MB at 6 zones** — two orders of magnitude of headroom on any reasonably sized card, so exact card capacity doesn't need confirming for this policy. (It would only matter for multi-month raw-resolution retention, which isn't being proposed — the 5s send cadence is a radio-liveness artifact, not a data requirement worth keeping at full resolution.)

Maintenance pass (inside the recorder, once per hour after a flush, no cron needed): roll up completed hours past `rollup_watermark` into `readings_hourly`, `DELETE FROM readings WHERE ts < now-90d`, `DELETE FROM readings_hourly WHERE ts < now-730d`, advance watermark, checkpoint WAL.

### Read path — Flutter app

The app already talks HTTP to the portal during pairing (`http://greenhouse.local/pair`), so history reuses that channel. New endpoints on `pi/portal/portal.py`, opened read-only per request (`sqlite3.connect('file:...?mode=ro', uri=True)`):

```
GET /api/history/series
  → [{"kind":"zone","zone":"zone1","metric":"air_temperature"}, ...]

GET /api/history?zone=zone1&metric=air_temperature&hours=24
GET /api/history?kind=weather&metric=temperature&hours=168
  → {"zone":"zone1","metric":"air_temperature","resolution":"minute",
     "points":[[ts, avg, min, max], ...]}
```

Resolution auto-selects: `hours <= 48` → minute table (optionally decimated server-side to ≤~600 points to keep chart payloads small); `hours > 48` → hourly table. No pairing-window gate (unlike `/pair`) — this is non-sensitive telemetry on a LAN-only port; acceptable for a thesis, with header-based auth reusing device credentials noted as future work.

**Known limitation:** remote app access rides the HiveMQ MQTT bridge; HTTP `:8080` is LAN-only, so charts only work on the greenhouse LAN. A future enhancement (recorder already holds an MQTT client) would be a request/response pair `greenhouse/history/request` → `greenhouse/history/response/{req_id}`, which would transparently traverse the bridge (`topic greenhouse/# both 1` in `hivemq-bridge.conf`). Deliberately deferred — see §9.

App-side additions: `app/lib/services/history_service.dart` (plain `http.get` against the LAN host), `app/lib/models/history_point.dart`, and a history chart reachable from a dashboard zone card tap.

### Read path — stateful automation rules

`weather.py` gets **direct read-only SQLite access**. Rule schema extends backward-compatibly — rules without `duration_minutes` behave exactly as today:

```json
{"id":"soil-dry-1h","name":"Irrigation on sustained dry soil","enabled":true,
 "trigger":{"metric":"zone1/soil_moisture","op":"<","value":30,"duration_minutes":60},
 "action":{"actuator":"pump1","command":"ON"},
 "cooldown_minutes":120}
```

Evaluation of a duration rule is one query per rule per cycle:

```sql
SELECT COUNT(*), SUM(avg < :thresh) FROM readings
WHERE series_id = :sid AND ts >= :now - :duration*60;
```

Fires when coverage is ≥~80% of expected buckets **and** all present buckets satisfy the condition. An in-memory `last_fired` map per rule enforces `cooldown_minutes` so a sustained condition doesn't republish `ON` every 30s cycle (in-memory reset on service restart is acceptable, noted as a known limitation). This also gives `weather.py` access to **zone** metrics for the first time — today its rules can only see live weather values.

**Gotcha:** WAL-mode readers need write access to the shared `-shm` file, and `greenhouse-weather.service` runs with `ProtectSystem=strict` — its unit needs `ReadWritePaths=/var/lib/greenhouse` added. Both recorder and weather run as `User=pi`; the portal runs as root. No permission conflicts.

---

## 6. Integration with Existing Conventions

- **`pi/install.sh`** (idempotent additions, matching existing style):
  - `apt-get install python3-paho-mqtt` added to the package list.
  - `mkdir -p /var/lib/greenhouse && chown pi:pi /var/lib/greenhouse` in the directories step.
  - `[ -f /etc/greenhouse/recorder.json ] || cat > …` default config (same guard pattern as `weather.json`/`rules.json`): `{"db_path":"/var/lib/greenhouse/greenhouse.db","flush_seconds":60,"raw_days":90,"hourly_days":730}`.
  - `cp systemd/greenhouse-recorder.service /etc/systemd/system/` + add to the `systemctl enable` line + restart at the end.
- **`pi/systemd/greenhouse-recorder.service`** mirrors the weather unit's style: `After=mosquitto.service` + `Requires=mosquitto.service`, `User=pi`, `Restart=always`, `RestartSec=10`, `TimeoutStopSec=15`, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/greenhouse`.
- **`pi/scripts/prep_image.sh`**: add `rm -f /var/lib/greenhouse/greenhouse.db*` (db + `-wal` + `-shm`) to the wipe list so cloned units ship with empty history, consistent with the existing per-unit identity wipe.
- **Config in `/etc/greenhouse/`, data in `/var/lib/greenhouse/`** — matches the existing split (mosquitto uses `/var/lib/mosquitto` the same way).

---

## 7. Testing / Verification

Additions to `pi/scripts/selftest.sh`, matching its existing `ok`/`no` style:
- `greenhouse-recorder` enabled and active (add to both existing service-check loops).
- DB present: `[ -f /var/lib/greenhouse/greenhouse.db ]`.
- Integrity + liveness via stdlib (no new sqlite3 CLI dependency): a `python3 -c` snippet opens read-only, runs `PRAGMA integrity_check`, and asserts at least one `readings` row with `ts` in the last 10 minutes — proves the whole MQTT→buffer→flush path, not just process liveness.
- History endpoint: `curl -s http://127.0.0.1:80/api/history/series` returns HTTP 200 (same pattern as the existing portal check).

Manual dev verification loop (documented, not automated): run `pi/tools/simulator.py --interval 5` for a few minutes, confirm row counts grow once per flush, curl `/api/history?zone=zone1&metric=air_temperature&hours=1`, then temporarily shrink `raw_days` in `recorder.json` to watch the rollup/prune pass work.

A small pure-function unit test for the minute-bucket aggregation and the duration-rule coverage logic is worthwhile; anything heavier is skipped deliberately for this project's scope.

---

## 8. Ordered Implementation Steps

1. **Create `pi/scripts/recorder.py`** — config load from `/etc/greenhouse/recorder.json`, schema bootstrap, paho subscription (retain-flag skip), minute-bucket buffer, 60s flush transaction, hourly rollup + retention pass, SIGTERM flush.
2. **Create `pi/systemd/greenhouse-recorder.service`** as specced in §6.
3. **Modify `pi/install.sh`** — package, directory, default config, unit install/enable/restart.
4. **Modify `pi/portal/portal.py`** — `/api/history` + `/api/history/series` read-only endpoints.
5. **Modify `pi/scripts/weather.py`** — duration-rule support in `eval_rules` (DB lookback + coverage + cooldown); **modify `pi/systemd/greenhouse-weather.service`** to add `ReadWritePaths=/var/lib/greenhouse`.
6. **Modify `pi/scripts/selftest.sh`** — checks from §7.
7. **Modify `pi/scripts/prep_image.sh`** — DB wipe on image prep.
8. **App:** create `app/lib/services/history_service.dart` + `app/lib/models/history_point.dart`; add a history chart screen/provider reachable from dashboard zone cards.
9. **Optional / adjacent (cheap, closes an existing gap):** have the recorder — the system's only long-lived MQTT subscriber — also handle `greenhouse/rules/update` (validate, write `/etc/greenhouse/rules.json`, touch the weather-reload flag) and `greenhouse/rules/get`, which the app already publishes with no Pi-side consumer today. Requires adding `/etc/greenhouse` and `/tmp` to the recorder's `ReadWritePaths`.

### Critical files
- `pi/scripts/recorder.py` (new — MQTT-to-SQLite writer, the core of this design)
- `pi/install.sh` (package/dir/config/unit integration)
- `pi/portal/portal.py` (history HTTP endpoints)
- `pi/scripts/weather.py` (duration-based stateful rules)
- `pi/systemd/greenhouse-weather.service` (needs `ReadWritePaths` for WAL reads; template for the new recorder unit)

---

## 9. Future Work (explicitly out of scope now)

- **External sync for ML/statistical analysis.** Nightly batch job pushing new rows (or just the small hourly rollups) from the Pi's SQLite DB to an external store (Postgres/Supabase is a good fit given existing tooling access) for things like monthly usage statistics or weather-forecast-accuracy comparisons (predicted vs. actual, using the already-stored `greenhouse/weather/forecast` data against later actual readings). Keep the Pi decoupled from the external service's uptime — push, don't depend on pull. Real-time streaming was considered and rejected for now: adds complexity and a hard dependency on the Pi's internet connection for no benefit at monthly-stats granularity.
- MQTT-based remote history RPC (`greenhouse/history/request`/`response`) so charts work over the HiveMQ bridge, not just LAN.
- Auth on the `/api/history` endpoint (reuse device credentials as a header).
- An `events(ts, type, payload_json)` audit table logging `greenhouse/weather/alert` and `greenhouse/actuators/+/set` traffic, for an "automation history" view in the app.
- Fixing `WEATHER_INTERVAL` (currently 30s, likely intended as minutes) and the bridge's missing `retain=true` — both noted in §1 as pre-existing issues, neither blocking this spec.
- Multi-hop sensor mesh / relay bridging for far-away nodes — tracked as a separate design.

---

*End of spec. Next: implementation plan via writing-plans.*
