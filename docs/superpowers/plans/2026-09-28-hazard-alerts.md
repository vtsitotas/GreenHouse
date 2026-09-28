# Hazard Alerts (fire, flood, frost, heat, drought) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (the user asked to start implementing in-session) or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect five greenhouse hazards per zone from the sensors the project already has, within one reading of them happening, and deliver them everywhere alerts already go: MQTT alert topic, phone push, LoRa uplink from remote sites, and the app.

**Architecture:**
- **A new Pi service, `hazard_monitor.py`.** `weather.py`'s rule engine runs every 30 min, far too slow for a fire, so the monitor subscribes to the live zone readings (`greenhouse/<zone>/air/temperature`, `…/air/humidity`, `…/soil/moisture`) and evaluates on every one.
- **Pure detection logic in `pi/shared/hazards.py`.** It is a small state machine per (zone, hazard):
  - instant or sustained conditions;
  - a rate-of-rise window for fire;
  - hysteresis (separate raise and clear thresholds), so an alert does not flap around a threshold;
  - a 6 h re-notify interval while a hazard stays active.
- **Delivery uses the existing path.** A raised hazard is published on `greenhouse/weather/alert`, the topic the app already listens to and `lora_uplink.py` already forwards as a LoRa fPort 2 alert. It is also pushed to the phone, gated by a new `hazard_alerts` notification setting.
- **Detection runs locally at every site.** A remote LoRa site's fire alert therefore leaves within seconds, not with the next 10-minute summary.

**Tech Stack:** Python 3 (Pi), paho-mqtt, pytest; Flutter/Dart (app).

**Spec:** this plan (design section below). Related: `docs/superpowers/specs/2026-07-10-customizable-alert-rules-design.md` (user rules, unchanged), `docs/superpowers/specs/2026-09-14-multi-site-lorawan-cellular-design.md` (alert uplink).

## Design

| Hazard | Trigger (defaults, all overridable in `/etc/greenhouse/hazards.json`) | Clears when | Severity | Action |
|---|---|---|---|---|
| `fire` | temp ≥ 55 °C, **or** within 10 min: temp rise ≥ 8 °C **and** humidity drop ≥ 15 points | temp < 45 °C and the rise condition is false | critical | — |
| `flood` | soil ≥ 95 % continuously for 30 min | soil < 90 % | warning | `pump1 OFF` (stop irrigating waterlogged soil) |
| `frost` | temp ≤ 2 °C (warning), ≤ 0 °C (critical) | temp > 3 °C | warning / critical | — |
| `heat` | temp ≥ 40 °C continuously for 10 min | temp < 38 °C | critical | — |
| `drought` | soil ≤ 15 % continuously for 60 min | soil > 20 % | warning | — |

- **Alert payload** on `greenhouse/weather/alert`: `{"type": "hazard-<kind>", "rule_id": "<kind>-<zone>", "severity", "message", "zone", "ts"}`. The `rule_id` is ≤ 40 ASCII characters, so it fits the LoRa alert encoding.
- **Raise only.** An alert is sent when a hazard becomes active, when its severity rises, and every 6 h while it stays active. Clearing is silent: nothing is sent over LoRa or push.
- **Retained readings are ignored.** They are stale at start-up and would trigger false alerts.
- **This detects with existing sensors, so it is not a real fire or flood sensor.** "Possible fire" means an abnormal heat event, and "flood" means saturated soil. A smoke or water-level sensor would need a firmware wire-format change, which stays out of scope and is noted as future work.

## Global Constraints

- Pi code style follows `pi/scripts/serial_bridge.py`: pure module-level helpers plus a thin `run()`. Tests use hand-written fakes and no mocking frameworks.
- Pi tests: `python -m pytest pi/tests/ -q`. The only allowed failure is the pre-existing Windows-only `test_nodes.py::test_store_is_written_0600…`.
- App: `flutter analyze` must be clean and `flutter test` must pass (Flutter at `/c/Users/billy/flutter/bin/flutter`).
- A new systemd unit gets the same sandboxing as `greenhouse-weather.service`, and must be both copied and enabled by `install.sh`.
- Remote-site data under `greenhouse/sites/…` is never evaluated by a gateway's monitor. Each site detects its own hazards.

## Review Focus

1. **A reading hovering exactly at a threshold**, e.g. temp alternating 1.9/2.1 °C, must raise frost once, not repeatedly. Pinned: `test_frost_does_not_flap_around_the_threshold`.
2. **The node goes silent while a sustained condition is true.** The flood timer keeps counting from its start, so an outage does not reset it; that is accepted. When the node returns, the next reading decides. Pinned: `test_sustained_condition_survives_a_reporting_gap`.
3. **A humidity reading arrives before any temperature reading in a zone.** No fire evaluation happens and nothing crashes. Pinned: `test_humidity_alone_never_raises_fire`.
4. **A non-numeric or garbage payload** is ignored, and the service does not die. Pinned: `test_garbage_payload_is_ignored`.
5. **The same hazard in two zones** gives two independent alerts, each with its own `rule_id`. Pinned: `test_two_zones_are_independent`.

---

### Task 1: `pi/shared/hazards.py` — detection state machine

**Files:** Create `pi/shared/hazards.py`, `pi/tests/test_hazards.py`.

**Interfaces (produced):**
- `DEFAULTS: dict`
- `merge_config(user: dict | None) -> dict`
- `@dataclass Event(kind, zone, severity, message, action: dict | None)`
- `class HazardMonitor(config=None)` with `add_reading(zone: str, metric: str, value: float, now: float) -> list[Event]`, where `metric` is `'temperature' | 'humidity' | 'soil'`
- `title(kind: str) -> str` (emoji + short label)
- `parse_rule_id(rule_id: str) -> tuple[str, str] | None`, which returns `(kind, zone)`

- [ ] **Step 1: Write the failing tests** in `pi/tests/test_hazards.py`:
  - fire:
    - `test_fire_on_absolute_temperature`
    - `test_fire_on_fast_rise_with_humidity_drop`
    - `test_fast_rise_without_humidity_drop_is_not_fire` (a sunny morning)
    - `test_humidity_alone_never_raises_fire`
  - flood:
    - `test_flood_needs_30_minutes_and_carries_the_pump_off_action`
    - `test_sustained_condition_survives_a_reporting_gap`
  - frost:
    - `test_frost_warning_then_critical_escalates_once`
    - `test_frost_does_not_flap_around_the_threshold`
  - heat and drought:
    - `test_heat_needs_10_minutes`
    - `test_drought_needs_60_minutes`
  - notification behaviour:
    - `test_active_hazard_renotifies_after_6_hours_only`
    - `test_hazard_clears_and_can_raise_again`
    - `test_two_zones_are_independent`
  - configuration:
    - `test_disabled_hazard_never_fires`
    - `test_user_config_overrides_one_threshold_and_keeps_the_rest`
  - helpers:
    - `test_rule_id_round_trips_and_fits_lora` (≤ 40 chars)
- [ ] **Step 2:** Run the tests and confirm they fail (module missing).
- [ ] **Step 3:** Implement per the Design table. There is one `_ZoneState` per zone holding the latest temp/humidity/soil, a 10-min `(ts, temp)` and `(ts, hum)` history deque, `since[kind]` and `active[kind] = (severity, last_notified)`. Sustained predicates use `since`: set it when the predicate first holds, reset it when the predicate is false. A hazard is active when `now − since ≥ for_minutes·60`.
- [ ] **Step 4:** Tests pass, then run the full Pi suite.
- [ ] **Step 5:** Commit `feat(pi): per-zone hazard detection (fire, flood, frost, heat, drought)`.

### Task 2: `hazard_monitor.py` service + settings + install

**Files:**
- Create `pi/scripts/hazard_monitor.py`, `pi/systemd/greenhouse-hazards.service`, `pi/tests/test_hazard_monitor.py`.
- Modify `pi/install.sh` (copy and enable the unit), `pi/scripts/weather.py` (`load_notification_settings` keeps `hazard_alerts`), `pi/tests/test_weather_rules.py` or the notification-settings test.

**Interfaces:**
- `parse_reading(topic, payload) -> (zone, metric, value) | None`
- `alert_payload(ev: Event, now) -> dict`
- `handle(monitor, topic, payload, retain, now, publish, push, push_enabled) -> list[Event]`

- [ ] **Step 1: Failing tests:**
  - `test_zone_readings_are_parsed_and_weather_and_sites_are_not`
  - `test_garbage_payload_is_ignored`
  - `test_retained_readings_are_ignored`
  - `test_a_raised_hazard_publishes_the_alert_the_action_and_a_push`
  - `test_push_is_skipped_when_hazard_alerts_are_off`
  - `test_notification_settings_keep_hazard_alerts` (weather.py)
- [ ] **Step 2:** Implement.
  - The service subscribes to the three zone reading patterns plus `greenhouse/settings/notifications/current`. `hazard_alerts` defaults to true.
  - It loads the optional `/etc/greenhouse/hazards.json`.
  - It publishes each alert to `greenhouse/weather/alert`, and each action to `greenhouse/actuators/<a>/set`.
  - Push uses `push.send_push(title(kind) + ' — ' + zone, message)`.
- [ ] **Step 3:** Create the unit, a copy of `greenhouse-weather.service` with a different `ExecStart` and `Description`, and add it to `install.sh`'s copy and enable lists.
- [ ] **Step 4:** Tests pass, then run the full suite.
- [ ] **Step 5:** Commit `feat(pi): greenhouse-hazards service publishes hazard alerts, actions and pushes`.

### Task 3: LoRa path

**Files:** Modify `pi/scripts/lora_gateway_bridge.py` (push text), `pi/tests/test_lora_gateway_bridge.py`.

- `lora_uplink.py` already forwards `greenhouse/weather/alert` with `severity` and `rule_id`. No change is needed; one test pins that a `critical` hazard alert is encoded with severity code 2.
- The gateway turns a hazard `rule_id` into a readable push: `"🔥 Possible fire — <site> / zone3"` instead of `"Rule fire-zone3 (critical)"`.

- [ ] **Step 1: Failing tests:**
  - `test_hazard_alert_from_a_remote_site_gets_a_readable_push`
  - `test_critical_hazard_alert_is_sent_as_severity_2` (uplink side, with `lora_payload.encode_alert`)
- [ ] **Step 2:** Implement with `hazards.title` and `hazards.parse_rule_id`.
- [ ] **Step 3:** Commit `feat(lora): hazard alerts from remote sites arrive as readable pushes`.

### Task 4: App

**Files:** Modify `app/lib/models/weather_alert.dart`, `app/lib/models/notification_settings.dart`, `app/lib/screens/weather/weather_screen.dart` (`_AlertSettingsCard`), and their tests.

- [ ] **Step 1: Failing tests:**
  - `WeatherAlert` titles for `hazard-fire|flood|frost|heat|drought`;
  - `isWarning` is true for `critical`;
  - a new `isCritical` getter;
  - `NotificationSettings.hazardAlerts` round-trips `hazard_alerts` (default true);
  - the widget test finds the switch `alert-settings-hazard-switch`.
- [ ] **Step 2:** Implement.
  - In "Recent alerts", critical alerts get `AppColors.danger` if it exists, otherwise `Colors.red`.
  - The new switch reads "Hazard alerts (fire, flood, frost, heat, drought)".
- [ ] **Step 3:** Run `flutter analyze` and `flutter test`.
- [ ] **Step 4:** Commit `feat(app): hazard alert titles, critical severity, hazard alert switch`.

### Task 5: Docs + deploy

- [ ] Write an INSTRUCTIONS section "Hazard alerts": the table above, the `hazards.json` override example, and the fact that these are detections from existing sensors and not certified fire or flood sensors.
- [ ] Add a HANDOFF bullet. The report gets a short note in §20 or §21 later.
- [ ] Deploy to the Pi when it is back: `install.sh` enables the new service. Check `systemctl is-active greenhouse-hazards` and `journalctl -u greenhouse-hazards`.
- [ ] Commit `docs: hazard alerts`.
