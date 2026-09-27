# Reliable CART Gate 0 Drift Bench — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Gate 0 clock-drift measurement trustworthy. Measure each sensor's **actual wake time** instead of the moment its reading reaches the Pi, then re-run both clock-source phases and decide Gate 0.

**Architecture:**
- **What went wrong.** The first run (2026-09-25..27) timed *Pi arrivals*. Send-path latency landed in the "step" figure as if it were clock instability: WiFi/ESP-NOW init, sensor warm-up, a 5 s parent search after a missed wake, and buffered bursts. Raw step came out at 0.95 %; robust statistics already brought it down to about 0.1 %.
- **The fix.** A bench-only build flag makes the fake-sensor firmware report its awake time (`millis()`, crystal-timed while awake) at the instant it seals the reading. It reuses the `battery_mv` field, which the Pi already republishes raw in the `/mesh` record. The logger records it together with the header rank. The analyzer then computes `wake = arrival − awake_ms/1000`, drops frames that were sealed while unrouted (rank 255), and reports robust statistics (median, 1.4826·MAD) alongside the classical ones.
- **Why wake times are the right quantity.** The sleep length already subtracts the awake time (`sleepMs = MESH_SLEEP_INTERVAL_MS − awake`, `goToSleep()`), so consecutive wake times are one RC-clocked period apart.

**Tech Stack:** Arduino/C++ on ESP32-C3 (`esp32:esp32` core, `arduino-cli`), Python 3 (Pi + host), pytest, paho-mqtt, systemd.

**Spec:** `docs/superpowers/specs/2026-09-23-cart-v2-revision.md` (§2 inputs, §6 Gate 0) and plan `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md` Task B4. This plan replaces B4's measurement method; B4's decision rule is unchanged.

## Global Constraints

- **Gate 0 rule (spec §6):** with the measured `b` and `step` at the chosen T, the simulated miss rate must be ≤ 1 %, sweeps ≤ 12/node/year, `G_max ≤ 20 s`, and relay worst-case awake ≤ its backstop. If the 136 kHz RC fails but RC_FAST_D256 passes, adopt RC_FAST (+0.12 mAh/day). If both fail: no-go.
- **Bench-only flags must never change a default build:**
  - `MESH_BENCH_REPORT_AWAKE_MS` is off by default.
  - `MESH_SLEEP_INTERVAL_MS` keeps `60000UL` unless overridden with `-D`.
  - `MESH_BENCH_RTC_FAST` already exists (Task B1).
- **All four sketches must compile** after any firmware change:
  - `esp32:esp32:esp32c3` for `edge_node_esp32_c3`, `fake_edge_node_esp32_c3` and `bridge_esp32`;
  - `esp32:esp32:esp32` for `edge_node_esp32`.
  - Tool: `/c/Users/billy/tools/arduino-cli/arduino-cli.exe --config-file /c/Users/billy/tools/arduino-cli/arduino-cli.yaml`.
- **Pi tests:** `python -m pytest pi/tests/ -q`. The only allowed failure is the pre-existing Windows-only `test_nodes.py::test_store_is_written_0600…`.
- **The logger's new CSV must stay readable by the old analyzer path**: column 1 is the MAC, column 2 is the arrival epoch, and new columns are appended.
- **Bench protocol (learned the hard way on 2026-09-26/27):**
  - Never open a sensor's or the bridge's USB serial port during a run. On ESP32-C3 USB-JTAG, opening the port toggles DTR/RTS and resets the board.
  - Keep the PC awake, since it powers the boards over USB.
  - Power the bridge from one source only.

## Review Focus

1. **A reading whose first send was lost and was resent next wake.** The Pi publishes it as *new*, carrying the previous wake's `awake_ms`, so the inferred wake time is about one period early. That interval must be excluded, not counted as a cycle. Pinned in Task 3 (`test_a_resent_reading_from_a_previous_wake_is_not_a_cycle`).
2. **Frames sealed while unrouted (rank 255),** which are sent seconds later or after buffering. They must be dropped in wake mode. Pinned in Task 3.
3. **Legacy 2-column rows** (the 2026-09-25..27 file). They must still analyse in `--mode arrival` and be ignored in `--mode wake`, never crash. Pinned in Task 3.
4. **A `/mesh` payload that is not JSON, or has `battery_mv: null`** (a node built without the flag, or the Pi's auth-failure record). The logger must still write `mac,arrival` with empty extra fields, and the analyzer must skip the row in wake mode. Pinned in Tasks 2 and 3.
5. **The wrong build flag goes on the wrong board** (bench flag missing, so `battery_mv` is a fake battery value of about 4000). Wake times would come out about 4 s early but still periodic, so the error would pass unnoticed. Task 5 Step 3 adds a live sanity check: `awake_ms` must be 1500–9000 and must vary between wakes.

---

### Task 1: Firmware — overridable sleep interval + bench awake-time reporting

**Files:**
- Modify: `firmware/libraries/GreenhouseMesh/mesh_config.h:60`
- Modify: `firmware/fake_edge_node_esp32_c3/fake_edge_node_esp32_c3.ino:232` (the `meshSetBatteryMv(readFakeBatteryMv());` in `runSleepyCycle()`)

**Interfaces:**
- Produces:
  - build flag `-DMESH_SLEEP_INTERVAL_MS=<ms>UL`, which overrides the default;
  - build flag `-DMESH_BENCH_REPORT_AWAKE_MS`, under which `battery_mv` carries `min(millis(), 65535)` at seal time.

- [ ] **Step 1: Make the interval overridable.** In `mesh_config.h`, replace line 60

```c
#define MESH_SLEEP_INTERVAL_MS    60000UL   // 1 min test duty cycle (change back to 900000UL for 15 min production)
```

with

```c
#ifndef MESH_SLEEP_INTERVAL_MS          // bench builds override with -DMESH_SLEEP_INTERVAL_MS=300000UL
#define MESH_SLEEP_INTERVAL_MS    60000UL   // 1 min test duty cycle (change back to 900000UL for 15 min production)
#endif
```

The existing `static_assert(MESH_DEDUP_WINDOW_MS < MESH_SLEEP_INTERVAL_MS, …)` still guards any override.

- [ ] **Step 2: Report awake time under the bench flag.** In `fake_edge_node_esp32_c3.ino`, replace the `meshSetBatteryMv(readFakeBatteryMv());` line inside `runSleepyCycle()` (the one directly before `bool delivered = sendWithConfirm(&r, deadline);`) with:

```cpp
#ifdef MESH_BENCH_REPORT_AWAKE_MS
  // Drift bench only: carry this wake's awake time (crystal-timed millis(),
  // taken right before the reading is sealed) in battery_mv, which the Pi
  // republishes raw in /mesh. wake time = Pi arrival - awake_ms/1000 strips
  // the send-path latency out of the clock measurement.
  meshSetBatteryMv((uint16_t)(millis() > 65535UL ? 65535UL : millis()));
#else
  meshSetBatteryMv(readFakeBatteryMv());
#endif
```

Leave the always-on `loop()` call at line ~389 unchanged, since bench nodes are sleepy.

- [ ] **Step 3: Compile the four default sketches** (Global Constraints command). Expected: each prints `Sketch uses …`, with no `error`.

- [ ] **Step 4: Compile the two bench variants and confirm the flags take effect** (from `firmware/`):

```bash
A=/c/Users/billy/tools/arduino-cli/arduino-cli.exe; C=/c/Users/billy/tools/arduino-cli/arduino-cli.yaml
$A --config-file $C compile --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc fake_edge_node_esp32_c3 2>&1 | grep "Sketch uses"
$A --config-file $C compile --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc --build-property "compiler.cpp.extra_flags=-DMESH_SLEEP_INTERVAL_MS=300000UL -DMESH_BENCH_REPORT_AWAKE_MS" fake_edge_node_esp32_c3 2>&1 | grep "Sketch uses"
$A --config-file $C compile --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc --build-property "compiler.cpp.extra_flags=-DMESH_SLEEP_INTERVAL_MS=300000UL -DMESH_BENCH_REPORT_AWAKE_MS -DMESH_BENCH_RTC_FAST" fake_edge_node_esp32_c3 2>&1 | grep "Sketch uses"
```

Expected: three different byte counts. A matching pair means a flag was not applied.

- [ ] **Step 5: Commit.**

```bash
git add firmware/libraries/GreenhouseMesh/mesh_config.h firmware/fake_edge_node_esp32_c3/fake_edge_node_esp32_c3.ino
git commit -m "bench: overridable sleep interval; fake sensor can report its awake time (drift bench)"
```

---

### Task 2: drift_logger records rank, awake_ms and rssi

**Files:**
- Modify: `pi/tools/drift_logger.py` (`format_line`, `on_message`)
- Test: `pi/tests/test_drift_logger.py`

**Interfaces:**
- Produces: `format_line(topic: str, payload: bytes, retain: bool, now: float) -> str | None`. It returns `"<MAC>,<arrival %.6f>,<rank>,<awake_ms>,<rssi>"`, with empty strings for missing values, or `None` for anything other than a live `/mesh` message.

- [ ] **Step 1: Replace the three `format_line` tests** in `pi/tests/test_drift_logger.py` (keep `test_every_connect_resubscribes`) with:

```python
MESH = 'greenhouse/nodes/206EF16C9DB0/mesh'


def _payload(**kw):
    import json
    base = {'parent': '206EF16CBE80', 'rank': 1, 'rssi': -61, 'sleepy': True,
            'battery_mv': 2345, 'zone': '9DB0', 'ts': 1}
    base.update(kw)
    return json.dumps(base).encode()


def test_live_mesh_message_becomes_a_csv_line_with_rank_awake_and_rssi():
    assert dl.format_line(MESH, _payload(), False, 1700000000.123456) \
        == '206EF16C9DB0,1700000000.123456,1,2345,-61'


def test_retained_replay_is_ignored():
    assert dl.format_line(MESH, _payload(), True, 1.0) is None


def test_other_topics_are_ignored():
    assert dl.format_line('greenhouse/nodes/206EF16C9DB0/status', b'online', False, 1.0) is None


def test_missing_values_are_left_empty():
    # Auth-failure records and nodes built without the bench flag.
    line = dl.format_line(MESH, _payload(battery_mv=None, rssi=None), False, 2.0)
    assert line == '206EF16C9DB0,2.000000,1,,'


def test_a_non_json_payload_still_logs_the_arrival():
    assert dl.format_line(MESH, b'not json', False, 3.0) == '206EF16C9DB0,3.000000,,,'
```

- [ ] **Step 2: Run and confirm the failures.** Run `python -m pytest pi/tests/test_drift_logger.py -q`. Expected: FAIL (`format_line` takes 3 arguments).

- [ ] **Step 3: Implement.** In `pi/tools/drift_logger.py`, add `import json` next to the other imports, then replace `format_line` with:

```python
def format_line(topic: str, payload: bytes, retain: bool, now: float):
    parts = topic.split('/')
    if retain or len(parts) != 4 or parts[0] != 'greenhouse' or parts[1] != 'nodes' \
            or parts[3] != 'mesh':
        return None
    try:
        rec = json.loads(payload)
        if not isinstance(rec, dict):
            rec = {}
    except (ValueError, UnicodeDecodeError):
        rec = {}

    def col(key):
        v = rec.get(key)
        return '' if v is None else str(v)

    # battery_mv carries awake-ms on MESH_BENCH_REPORT_AWAKE_MS builds.
    return f'{parts[2]},{now:.6f},{col("rank")},{col("battery_mv")},{col("rssi")}'
```

and in `main()` change the `on_message` body to:

```python
        line = format_line(msg.topic, msg.payload, msg.retain, time.time())
```

- [ ] **Step 4: Run the file.** Run `python -m pytest pi/tests/test_drift_logger.py -q`. Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add pi/tools/drift_logger.py pi/tests/test_drift_logger.py
git commit -m "bench: drift_logger records rank, awake_ms and rssi per /mesh arrival"
```

---

### Task 3: drift_analyze — wake-time mode, rank filter, robust statistics

**Files:**
- Modify: `pi/tools/drift_analyze.py`
- Test: `pi/tests/test_drift_analyze.py`

**Interfaces:**
- Consumes: CSV rows from Task 2 (`mac,arrival,rank,awake_ms,rssi`) and legacy rows (`mac,arrival`).
- Produces:
  - `load_times(lines, mode='arrival') -> dict[str, list[float]]`. `mode` is `'arrival'` or `'wake'`. The existing call `load_times(lines)` keeps working.
  - `_stats(values)` returns the existing keys `bias`, `step`, `n` plus `bias_median` and `step_robust`, where `step_robust` is 1.4826 × MAD of consecutive differences.
  - `main()` gains `--mode {arrival,wake}`, defaulting to `wake`. Its `worst` block uses `bias_median` / `step_robust`, with `worst_classic` keeping the old pair.

- [ ] **Step 1: Write the failing tests.** Append to `pi/tests/test_drift_analyze.py`:

```python
def _row(mac, arrival, rank='1', awake_ms='3000', rssi='-60'):
    return f'{mac},{arrival},{rank},{awake_ms},{rssi}\n'


M = '206EF16C6B50'


def test_wake_mode_subtracts_the_awake_time():
    rows = [_row(M, 1003.0, awake_ms='3000'), _row(M, 1305.5, awake_ms='5500')]
    assert da.load_times(rows, mode='wake')[M] == [1000.0, 1300.0]


def test_wake_mode_drops_frames_sealed_while_unrouted():
    rows = [_row(M, 1003.0), _row(M, 1310.0, rank='255'), _row(M, 1603.0)]
    assert da.load_times(rows, mode='wake')[M] == [1000.0, 1600.0]


def test_wake_mode_ignores_legacy_and_incomplete_rows():
    rows = ['206EF16C6B50,1003.0\n', _row(M, 1303.0, awake_ms=''), _row(M, 1603.0)]
    assert da.load_times(rows, mode='wake')[M] == [1600.0]


def test_arrival_mode_still_reads_legacy_and_new_rows():
    rows = ['206EF16C6B50,1003.0\n', _row(M, 1303.0)]
    assert da.load_times(rows)[M] == [1003.0, 1303.0]


def test_a_resent_reading_from_a_previous_wake_is_not_a_cycle():
    # Wake k's reading never reached the Pi; wake k+1 resends it (the Pi sees
    # it as new) carrying wake k's awake_ms, so its inferred "wake" lands a
    # fraction of a second away from wake k+1's own. That near-zero interval
    # must not be counted as a cycle, and no cycle may pick up a large error.
    times = [1000.0, 1300.0, 1300.4, 1600.0]
    rates = da.per_node_rates(times, 300)
    assert len(rates) == 2                      # the 0.4 s interval is dropped
    assert all(abs(r) < 0.002 for _t, r in rates)


def test_robust_step_ignores_a_single_outlier_cycle():
    vals = [0.001, 0.0011, 0.0009, 0.001, 0.02, 0.001, 0.0011]
    s = da._stats(vals)
    assert s['step_robust'] < 0.001 < s['step']
    assert abs(s['bias_median'] - 0.001) < 1e-9
```

- [ ] **Step 2: Run and confirm the failures.** Run `python -m pytest pi/tests/test_drift_analyze.py -q`. Expected: FAIL (`load_times() got an unexpected keyword argument 'mode'`, KeyError `step_robust`).

- [ ] **Step 3: Implement.** In `pi/tools/drift_analyze.py`, replace `_stats` with:

```python
def _stats(values):
    if len(values) == 0:
        return {'bias': 0.0, 'step': 0.0, 'n': 0, 'bias_median': 0.0, 'step_robust': 0.0}
    if len(values) == 1:
        return {'bias': values[0], 'step': 0.0, 'n': 1,
                'bias_median': values[0], 'step_robust': 0.0}
    diffs = [b - a for a, b in zip(values, values[1:])]
    med = statistics.median(diffs)
    mad = statistics.median([abs(d - med) for d in diffs])
    return {'bias': statistics.mean(values),
            'step': statistics.pstdev(diffs) if len(diffs) > 1 else 0.0,
            'n': len(values),
            # Robust twins: arrival/wake jitter produces isolated outlier cycles
            # that would otherwise dominate the classical stdev.
            'bias_median': statistics.median(values),
            'step_robust': 1.4826 * mad}
```

Replace `load_times` with:

```python
def load_times(lines, mode='arrival'):
    """Per-node event times from logger rows.

    arrival: column 2 (works for legacy 2-column rows too).
    wake:    arrival - awake_ms/1000, only for rows sealed while routed
             (rank 1..254) that carry awake_ms (MESH_BENCH_REPORT_AWAKE_MS
             builds). Rows damaged by a power cut are skipped in both modes.
    """
    times = defaultdict(list)
    for row in csv.reader(line.replace(chr(0), '') for line in lines):
        if len(row) < 2 or not _MAC.match(row[0].strip()):
            continue
        try:
            t = float(row[1])
        except ValueError:
            continue
        if mode == 'wake':
            if len(row) < 4:
                continue
            try:
                rank = int(row[2])
                awake_ms = int(row[3])
            except ValueError:
                continue
            if not 1 <= rank <= 254:
                continue
            t -= awake_ms / 1000.0
        times[row[0].strip()].append(t)
    return times
```

In `main()`:
- add `ap.add_argument('--mode', choices=('arrival', 'wake'), default='wake')`;
- change the load to `times = load_times(fh, mode=args.mode)`;
- replace the worst/print block with:

```python
    worst_bias = max((abs(p['bias_median']) for p in pairs.values()), default=0.0)
    worst_step = max((p['step_robust'] for p in pairs.values()), default=0.0)
    print(json.dumps({'period_s': args.period, 'mode': args.mode,
                      'per_node': {m: summarize(r) for m, r in rates.items()},
                      'pairs': pairs,
                      'worst': {'bias': worst_bias, 'step': worst_step},
                      'worst_classic': {
                          'bias': max((abs(p['bias']) for p in pairs.values()), default=0.0),
                          'step': max((p['step'] for p in pairs.values()), default=0.0)}},
                     indent=2))
```

Also drop any node with fewer than 20 clean cycles from `pairs` (a node that barely reported cannot define the worst case):

```python
    rates = {mac: per_node_rates(ts, args.period) for mac, ts in times.items()}
    rates = {m: r for m, r in rates.items() if len(r) >= 20}
```

- [ ] **Step 4: Run the file, then the full suite.** Run `python -m pytest pi/tests/test_drift_analyze.py -q`: all pass. Run `python -m pytest pi/tests/ -q`: only the pre-existing Windows failure.

- [ ] **Step 5: Re-analyse the first run for the record.** The 2026-09-25..27 file, in arrival mode:

```bash
python pi/tools/drift_analyze.py <copy of /home/pi/bench/drift_rc.csv> --period 300 --mode arrival
```

Expected: worst (robust) bias ≈ 0.17 %, step ≈ 0.11 %; worst_classic step ≈ 0.95 %. Keep the JSON for the Task 6 write-up.

- [ ] **Step 6: Commit.**

```bash
git add pi/tools/drift_analyze.py pi/tests/test_drift_analyze.py
git commit -m "bench: drift_analyze wake-time mode, rank filter, robust statistics"
```

---

### Task 4: Bench kit — logger unit + runbook

**Files:**
- Create: `pi/tools/drift-bench@.service`. It lives in `pi/tools/`, **not** `pi/systemd/`, so `install.sh` never installs it on a real unit.
- Create: `docs/BENCH_DRIFT.md`

- [ ] **Step 1: Create the templated unit** `pi/tools/drift-bench@.service`:

```ini
# CART Gate 0 drift bench logger. Bench-only: copied by hand per
# docs/BENCH_DRIFT.md, never installed by install.sh.
#   sudo cp pi/tools/drift-bench@.service /etc/systemd/system/
#   sudo systemctl enable --now drift-bench@rc      # -> /home/pi/bench/drift_rc.csv
[Unit]
Description=CART Gate 0 drift bench logger (%i)
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
User=pi
Group=pi
ExecStart=/usr/bin/python3 /home/pi/bench/drift_logger.py --out /home/pi/bench/drift_%i.csv
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write `docs/BENCH_DRIFT.md`** with these sections (full text, no placeholders):

1. **Purpose** — what Gate 0 decides, with a link to spec §6.
2. **Why wake time, not arrival time** — two sentences plus the `wake = arrival − awake_ms/1000` formula.
3. **Hardware setup:**
   - 2–3 C3 sensors enrolled `sleepy=true`, all in direct bridge range, in the **same thermal environment**; for a realistic result, where day/night temperature actually changes.
   - The bridge powered from one source only.
   - The PC kept awake if it powers the boards (Windows: Sleep = Never).
4. **Protocol rules:**
   - **Never open a board's USB serial port during a run** (DTR/RTS resets an ESP32-C3).
   - Don't move the boards.
   - Don't redeploy the Pi (`install.sh` deletes `/home/pi/greenhouse`; the logger copy lives in `/home/pi/bench`).
   - Note any power event.
5. **Flash (no erase, enrolment kept):**
   - Put the sensor in download mode (hold BOOT, tap RESET).
   - Run the phase A command:
     ```bash
     arduino-cli compile --upload -p COMx --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc --build-property "compiler.cpp.extra_flags=-DMESH_SLEEP_INTERVAL_MS=300000UL -DMESH_BENCH_REPORT_AWAKE_MS" firmware/fake_edge_node_esp32_c3
     ```
   - For phase B, add `-DMESH_BENCH_RTC_FAST`.
   - If a board stays in download mode after the upload, run `esptool --port COMx --after watchdog-reset read-mac`.
6. **Start logging:**
   - `mkdir -p /home/pi/bench && cp /home/pi/greenhouse/tools/drift_logger.py /home/pi/bench/`
   - Install the unit (Step 1 commands).
   - Phase A instance `rc2`, phase B instance `fast`.
   - Before starting phase B: `sudo systemctl disable --now drift-bench@rc2`.
7. **Health checks** (at +15 min, then daily): `tail /home/pi/bench/drift_<phase>.csv`. Every sensor must appear with rank 1 and awake_ms between 1500 and 9000, and awake_ms must vary between rows. `journalctl -u greenhouse-serial-bridge | grep 'exiting so systemd'` must be empty or rare.
8. **Analyse:**
   ```bash
   python3 pi/tools/drift_analyze.py drift_<phase>.csv --period 300 --mode wake
   ```
   Then feed `worst.bias` and `worst.step` into `docs/analysis/cart_sim.py`: set `MEASURED["bias"]`, `MEASURED["step_300"]`, and `step_900 = 3 × step`. Run it, then read section A2 "margin" rows.
9. **Decide:** the Gate 0 rule, verbatim from spec §6.

- [ ] **Step 3: Commit.**

```bash
git add pi/tools/drift-bench@.service docs/BENCH_DRIFT.md
git commit -m "bench: templated drift logger unit + Gate 0 bench runbook"
```

---

### Task 5: Run phase A — default RC clock, wake-time method (≥ 24 h, hardware)

- [ ] **Step 1: Stop the old run and archive its file.** Run `sudo systemctl disable --now drift-bench && sudo rm /etc/systemd/system/drift-bench.service`, then `mv /home/pi/bench/drift_rc.csv /home/pi/bench/drift_rc_arrival_2026-09-25.csv`. This is the first run, which used arrival timing.
- [ ] **Step 2: Flash** 6B50, 75EC and 9DB0 with the phase A command (runbook §5). Deploy the Task 2 logger to `/home/pi/bench/`, install the unit, and start `drift-bench@rc2`.
- [ ] **Step 3: Sanity check at +15 min** (Review Focus 5). Every sensor has ≥ 2 rows with rank 1, and awake_ms is in 1500–9000 and not constant. If a sensor shows awake_ms ≈ 4000 on every row, it was flashed without the flag: reflash it.
- [ ] **Step 4: Decide what to do about 9DB0.** Look at its `rssi` column. It dropped out for 3.8 h on 2026-09-27 while waking normally. If its median RSSI is 10 dB or more below the others, move it closer to the bridge or rotate its antenna **before** the run counts; note the change in the runbook log. The analyzer drops it from the pairs if it has fewer than 20 clean cycles.
- [ ] **Step 5: Leave it running ≥ 24 h, covering a night and a day.** Do the daily health check (runbook §7).

### Task 6: Run phase B — RC_FAST_D256 (≥ 24 h, hardware) and decide Gate 0

- [ ] **Step 1:** Run `sudo systemctl disable --now drift-bench@rc2`. Reflash the same boards with the phase B command (adds `-DMESH_BENCH_RTC_FAST`). Run `sudo systemctl enable --now drift-bench@fast`, then do the +15 min sanity check.
- [ ] **Step 2:** Leave it running ≥ 24 h, with daily health checks.
- [ ] **Step 3: Analyse both phases.** Run `drift_analyze.py … --mode wake` on `drift_rc2.csv` and on `drift_fast.csv`. Then run `cart_sim.py` once per phase with that phase's `worst` values, from a copy of the script with `MEASURED` edited; the script writes `cart_sim_results.txt` next to itself.
- [ ] **Step 4: Decide with the spec §6 rule**:
  - PASS RC → keep the default clock;
  - FAIL RC, PASS FAST → Part C adds the RC_FAST switch;
  - FAIL both → no-go.
  - Then choose T.
- [ ] **Step 5: Record the results.**
  - Append a "Gate 0 results (2026-09-2x)" subsection to spec §5, containing:
    - both JSON outputs;
    - the first run's arrival-mode numbers, as the method comparison;
    - the `cart_sim` A2 rows used;
    - the decision.
  - Add a HANDOFF TL;DR.
  - Bring the report's §21 up to date.
  - Commit with `docs: CART Gate 0 drift results and decision`.
- [ ] **Step 6: Restore normal firmware.** Delete the local `bench/cart-gate0-drift` branch (replaced by the `-D` override). If the boards should return to the 60 s test interval, reflash them without the bench flags.
