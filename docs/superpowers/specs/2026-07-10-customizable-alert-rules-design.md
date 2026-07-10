# Customizable Alert Rules & Notification Preferences — Design Spec

**Date:** 2026-07-10
**Status:** Approved, ready for implementation planning

## Background

The FCM push-notifications feature (see
`docs/superpowers/specs/2026-07-10-fcm-push-notifications-design.md`) just
shipped and was bench-verified end-to-end. The next ask was to add
per-zone "soil too dry" and "too humid" alerts — but the request evolved
once the underlying rule engine was inspected: hardcoding specific
percentages (e.g. dry < 15% for 2 days, humid > 70% for 24h) doesn't fit,
since "each farmer or plant wants it different." The goal shifted to making
the whole alert-rule system genuinely customizable, with the specific
dry/humid numbers becoming an example use of that general capability
rather than a special case.

Inspecting the current implementation surfaced real gaps:

- `pi/scripts/weather.py`'s rule engine (`eval_rules`/`eval_duration_rule`)
  already supports zone-prefixed metrics (`"zone1/soil_moisture"`) and
  sustained-duration conditions (`trigger.duration_minutes`) — but **only**
  for rules with a `duration_minutes` set. Instant (non-duration) rules only
  ever see ambient weather metrics (`temperature`, `humidity`, `wind_kmh`,
  `uv_index`, `rain_mm_1h` — all from the Open-Meteo forecast), never zone
  sensor data. This is a real, pre-existing boundary, not something this
  project changes.
- The app's Rules UI (`app/lib/screens/weather/weather_screen.dart`'s
  `_RulesTab`/`_RulesList`/`_RuleCard`) can only edit the **threshold value**
  of a rule that already exists in `/etc/greenhouse/rules.json` — it cannot
  create a new rule, has no zone or metric picker, no duration field, and
  every rule unconditionally requires an actuator action (`WeatherRule`'s
  `actuatorId`/`command` are non-nullable).
- Every rule that fires today calls `push.send_push()` unconditionally
  (added in the FCM feature) — there's no way to get the automation action
  without the notification, or vice versa, and no way to turn off the two
  built-in system alerts (frost forecast, daily summary) at all.

## Goals

1. Let users create, edit, and delete fully custom automation rules from
   the app — any zone sensor metric or weather metric, any operator,
   threshold, optional sustained-duration, and an **optional** actuator
   action (rules can be alert-only).
2. Let each rule independently control whether it sends a push
   notification (`notify`) separate from whether its automation action is
   active (`enabled`) — e.g. silently auto-water without a notification, or
   get notified without an automated action.
3. Let users toggle the two built-in system alerts (frost forecast, daily
   summary) on/off, independent of any rule.
4. Concretely seed the three zones (`zone1`, `zone2`, `zone3`) with the
   specific dry/humid rules requested this session — soil moisture < 15%
   sustained 2 days (2880 min), air humidity > 70% sustained 24 hours (1440
   min) — as regular rules created through this same general mechanism, not
   hardcoded specially. These are a starting point the farmer can edit or
   delete like any other rule.

## Non-goals

- No live (instant, non-duration) evaluation path for zone-specific
  metrics. Zone-prefixed metrics remain duration-only, per the existing
  architectural boundary described above — the rule builder UI enforces
  this (duration is required whenever a zone is selected, optional for
  weather-level metrics).
- No change to `pi/shared/push.py`'s `send_push()` signature or internals.
  Category/preference filtering happens entirely at the call sites in
  `weather.py`, which already decide title/body — they now also decide
  whether to call `send_push()` at all.
- No retroactive migration UI for the 3 pre-existing rules (`rain-close`,
  `frost-heat`, `heat-fan`) — they gain a `notify: true` default like any
  rule missing the field, and remain editable through the new general UI
  same as any other rule.
- No undo/history for rule changes — matches the existing rules UI's
  immediate-sync-on-edit behavior (`publishRules()` pushes the full list to
  the Pi right away).

## Architecture

### Rule schema changes (`rules.json` / `WeatherRule`)

Each rule gains:

- `action` becomes **optional**. When absent, the rule is alert-only — no
  actuator command is published when it fires.
- `trigger.duration_minutes` becomes a **first-class, editable field** in
  the app (already read by the Pi backend; the app model never exposed it).
- `notify: bool` (default `true` if absent) — controls whether
  `push.send_push()` is called when the rule fires. Independent of
  `enabled`, which continues to gate the whole rule (both the actuator
  action and the notification).

Example seeded rule (one of six — three zones × dry/humid):

```json
{
  "id": "zone1-dry", "name": "Zone 1 soil dry", "enabled": true, "notify": true,
  "trigger": {"metric": "zone1/soil_moisture", "op": "<", "value": 15, "duration_minutes": 2880}
}
```

Note there is no `"action"` key at all — this is a genuine alert-only rule,
not an empty/null placeholder.

### Pi-side changes (`pi/scripts/weather.py`)

`_fire(rule, message)` changes from unconditionally requiring `rule['action']`
to:

```python
def _fire(rule, message):
    action = rule.get('action')
    if action:
        actuator = action['actuator']
        command = action['command']
        topic = f'greenhouse/actuators/{actuator}/set'
        print(f'[weather] Rule "{rule.get("name")}" triggered → {topic} {command}', flush=True)
        mqtt_publish(topic, command)
    alert = {
        'type': rule.get('id', 'rule'),
        'message': message,
        'severity': 'warning',
        'rule_id': rule.get('id'),
    }
    mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
    if rule.get('notify', True):
        send_push(rule.get('name', 'Rule'), message)
```

The MQTT alert publish is unconditional (unchanged) — `notify` only gates
the push call, matching the existing FCM spec's principle that MQTT and
push are independent channels.

### Built-in alert settings (frost forecast, daily summary)

These aren't rules, so they get a small settings blob synced the same
lightweight way `_pull_location_from_mqtt()` already works: the app
publishes retained JSON to `greenhouse/settings/notifications`:

```json
{"frost_forecast": true, "daily_summary": true}
```

`weather.py` gains `_pull_notification_settings()` (mirrors
`_pull_location_from_mqtt()` exactly — `mosquitto_sub -C 1 -W 2`, on failure
keep the last-loaded value) and `load_notification_settings()` (mirrors
`load_location()` — reads `/etc/greenhouse/notification_settings.json`,
defaults both fields to `true` if the file doesn't exist yet). Both new
alert functions gain a settings check:

- `maybe_send_frost_alert(data)`: only calls `send_push(...)` if
  `settings.get('frost_forecast', True)`. The `mqtt_publish` stays
  unconditional.
- `maybe_send_daily_summary(data, metrics)`: only calls `send_push(...)` if
  `settings.get('daily_summary', True)`. The `mqtt_publish` stays
  unconditional.

### App-side model changes (`app/lib/models/weather_rule.dart`)

`WeatherRule` restructures to hold `zone` (`String?`, e.g. `"zone1"`, `null`
for weather-level) and `metric` (bare metric name, e.g. `"soil_moisture"`)
**separately**, rather than one opaque `triggerMetric` string — the zone
picker and metric picker map directly to these two fields, and the full
wire-format metric string (`"zone1/soil_moisture"` or bare `"temperature"`)
is composed only in `toJson()`/parsed only in `fromJson()`. `actuatorId` and
`command` become nullable (`String?`). New fields: `durationMinutes` (`int?`)
and `notify` (`bool`, default `true`).

`metricLabel` extends to describe zone metrics (e.g. "Zone 1 — Soil
moisture (%)") in addition to the existing weather-metric labels.

### App-side Rules UI (`app/lib/screens/weather/weather_screen.dart`)

The existing threshold-only edit dialog is replaced by a full rule form
(used for both "Add rule" and "Edit rule"), covering:

- **Zone**: a dropdown of `"Weather"` plus every zone currently known to
  the app (sourced from `readingsProvider`'s live zone keys, the same
  dynamic-discovery pattern the dashboard already uses — zones aren't a
  fixed enum anywhere in this codebase).
- **Metric**: options depend on the zone selection — weather metrics
  (temperature, rain next hour, humidity, wind, UV index) when "Weather" is
  selected; zone sensor metrics (soil moisture, air humidity, air
  temperature) when a zone is selected.
- **Operator**: `>`, `<`, `>=`, `<=`, `==`.
- **Threshold**: numeric field (existing behavior, reused).
- **Duration**: optional for weather metrics, **required** when a zone is
  selected (enforced in the form, matching the real backend boundary
  described in Non-goals).
- **Action** (optional): a toggle "Also control a device" reveals an
  actuator picker (sourced from `actuatorsProvider`'s live keys, same
  dynamic-discovery pattern) and an ON/OFF command picker. Left off, the
  rule is alert-only.
- **Notify**: a toggle, default on.
- **Name**: text field.

Existing rule cards gain a quick-access `notify` toggle switch and a delete
action, alongside the existing enabled toggle; tapping a card opens the
same full form pre-filled for editing (replacing today's threshold-only
dialog).

A new "Alert settings" area (above or alongside the rules list) holds two
toggles: "Frost forecast alerts" and "Daily weather summary" — publishing
to `greenhouse/settings/notifications` (retained) via a new
`GreenhouseRepository.publishNotificationSettings(...)` method, mirroring
the existing `publishLocation()`/`publishRules()` pattern.

### Planning-time discovery: rule edits never actually reached the Pi

While writing the implementation plan, inspecting `greenhouse/rules/update`'s
handling on the Pi turned up a pre-existing bug, unrelated to anything in
this spec: `weather.py` has no persistent MQTT client (only CLI-based
`mosquitto_pub`/`mosquitto_sub` polling for the handful of topics it
explicitly checks), and `rules/update` was never one of them. The app's
`publishRules()` call — and the "Changes sync to the Pi immediately" message
next to the rules list — has therefore never actually updated
`rules.json` on the Pi; edits only lived in the app's own local state until
the next refresh discarded them. Since this feature's entire premise is
that rule edits/creates persist, this is fixed as this plan's first task,
using the exact pattern already proven for location sync: `publishRules()`
gains `retain: true` (matching `publishLocation()`), and `weather.py` gains
a `_pull_rules_from_mqtt()` poller (matching `_pull_location_from_mqtt()`
exactly) that writes the retained payload to `RULES_CFG` on disk.

## Error Handling

- Same as the existing rules sync: `publishRules()` always sends the full
  list; a malformed rule (e.g. zone selected with no duration) is prevented
  at the form level, not validated server-side beyond what already exists
  (`weather.py`'s per-rule `try/except` in `eval_rules()`'s loop already
  isolates one bad rule from breaking the others).
- Missing `notify`/`action` fields on old-format rules default to
  `true`/absent respectively (`rule.get('notify', True)`, `rule.get('action')`)
  — no migration script needed, existing rules keep working unchanged.
- Missing `/etc/greenhouse/notification_settings.json` defaults both
  built-in toggles to `true` (alerts on by default, matching current
  behavior before this feature existed).

## Testing

Scoped proportionally to a thesis project:

- `pi/tests/test_weather_rules.py`: `_fire()` with a rule that has no
  `action` key (no actuator publish, alert still fires); a rule with
  `notify: false` (mqtt_publish still happens, `send_push` is not called);
  `maybe_send_frost_alert`/`maybe_send_daily_summary` respecting
  `frost_forecast: false`/`daily_summary: false` settings (no `send_push`
  call, `mqtt_publish` still happens).
- `pi/tests/test_push.py` unaffected — `send_push()` itself isn't changing.
- `app/test/models/weather_rule_test.dart` (new): `fromJson`/`toJson`
  round-tripping with and without `action`, with `duration_minutes`, with
  `notify` defaulting to `true` when absent; `zone`/`metric` composing into
  the correct wire-format string.
- `app/test/widgets/weather_screen_test.dart` (existing file, extended):
  the rule form requires duration when a zone is selected; creating an
  alert-only rule (no action) round-trips through `publishRules()`
  correctly; the two built-in alert toggles publish the expected retained
  JSON.

## Follow-up (explicitly deferred)

- A live (instant) evaluation path for zone-specific metrics — would
  require weather.py to hold onto the latest zone sensor readings the way
  it already holds onto weather `metrics`, a real architectural change, not
  attempted here.
- Per-device notification preferences (e.g. one household member wants
  fewer alerts than another) — out of scope; today's toggles are
  Pi-global, not per-registered-device, unlike the FCM token registry.
