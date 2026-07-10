# Customizable Alert Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the rule engine and its (currently threshold-edit-only) app UI into a real rule builder — any zone/weather metric, optional sustained-duration, optional actuator action, and a per-rule notify toggle — plus on/off toggles for the two built-in system alerts, and seed the three zones with the specific dry/humid rules requested this session.

**Architecture:** `rules.json` gains `notify` (bool) and makes `action` optional. A pre-existing bug (rule edits from the app never actually reach the Pi — nothing subscribes to `greenhouse/rules/update`) gets fixed using the exact pattern already proven for location sync. A new small settings file (`notification_settings.json`) and matching retained MQTT topic control the two built-in alerts, synced the same way. The app's `WeatherRule` model splits `zone`/`metric` apart (from one opaque string) and gains `durationMinutes`/`notify`; a new rule-builder dialog replaces the existing threshold-only editor.

**Tech Stack:** Python (`pi/scripts/weather.py`), Flutter/Dart (Riverpod, mocktail for tests).

## Global Constraints

- Zone-specific metrics (soil moisture, zone air humidity/temperature) are duration-only — there is no live/instant evaluation path for them, only for weather-level metrics. The rule form must require a duration whenever a zone is selected, and must not offer zone metrics without it.
- `notify` defaults to `true` when absent (existing rules keep alerting as before). `action` absent means alert-only (no actuator command published).
- The two built-in alerts (frost forecast, daily summary) default both to `true` when no settings file exists yet.
- No changes to `pi/shared/push.py` — filtering happens entirely at `weather.py`'s call sites.
- The three pre-existing rules (`rain-close`, `frost-heat`, `heat-fan`) are not migrated/rewritten — they keep working via the `notify` default.

---

### Task 1: Fix rules sync (app never actually reached the Pi)

**Files:**
- Modify: `app/lib/repository/greenhouse_repository.dart:114-121` (`publishRules`)
- Modify: `pi/scripts/weather.py` (add `_pull_rules_from_mqtt()`, wire into `run()`)
- Test: `app/test/repository/greenhouse_repository_test.dart`, `pi/tests/test_weather_rules.py`

**Interfaces:**
- Produces: `weather.py::_pull_rules_from_mqtt() -> None` (writes `RULES_CFG` if a new retained message is found, touches `RELOAD_FLAG`), used by later tasks' rule-loading flow unchanged (`load_rules()` already re-reads from disk every cycle).

- [ ] **Step 1: Write the failing app-side test**

In `app/test/repository/greenhouse_repository_test.dart`, add (there should already be a `MockConnection conn`/`GreenhouseRepository repo` set up in `setUp()` — reuse it):

```dart
  test('publishRules retains the message so the Pi can poll and catch it', () async {
    await repo.publishRules([
      const WeatherRule(
        id: 'r1', name: 'Test', enabled: true, notify: true,
        zone: null, metric: 'temperature', op: '>', value: 30.0,
        durationMinutes: null, actuatorId: 'fan1', command: 'ON',
      ),
    ]);

    verify(() => conn.publishRaw(
          'greenhouse/rules/update',
          any(),
          retain: true,
        )).called(1);
  });
```

(This test references `WeatherRule`'s new constructor shape from Task 5 — if Task 5 hasn't run yet in your execution order, use the *current* `WeatherRule` constructor instead: `WeatherRule(id: 'r1', name: 'Test', enabled: true, triggerMetric: 'temperature', op: '>', value: 30.0, actuatorId: 'fan1', command: 'ON')`. Either compiles against `publishRules(List<WeatherRule>)`'s existing signature.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && flutter test test/repository/greenhouse_repository_test.dart`
Expected: FAIL — `publishRaw` was called with `retain: false` (or the default), not `true`.

- [ ] **Step 3: Fix `publishRules` to retain**

In `app/lib/repository/greenhouse_repository.dart`, change:

```dart
  Future<void> publishRules(List<WeatherRule> rules) async {
    _rules = rules;
    _rulesCtrl.add(List.from(_rules));
    await connection.publishRaw(
      'greenhouse/rules/update',
      WeatherRule.listToJson(rules),
    );
  }
```

to:

```dart
  /// Retained so weather.py's poller (_pull_rules_from_mqtt) reliably picks
  /// this up regardless of exact timing, the same reason publishLocation
  /// retains its message.
  Future<void> publishRules(List<WeatherRule> rules) async {
    _rules = rules;
    _rulesCtrl.add(List.from(_rules));
    await connection.publishRaw(
      'greenhouse/rules/update',
      WeatherRule.listToJson(rules),
      retain: true,
    );
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && flutter test test/repository/greenhouse_repository_test.dart`
Expected: all tests in the file pass.

- [ ] **Step 5: Write the failing Pi-side test**

Add to `pi/tests/test_weather_rules.py`:

```python
def test_pull_rules_from_mqtt_writes_valid_payload(monkeypatch, tmp_path):
    rules_file = tmp_path / 'rules.json'
    rules_file.write_text('[]')
    monkeypatch.setattr(weather, 'RULES_CFG', str(rules_file))

    new_rules = '[{"id":"r1","name":"Test","enabled":true,"trigger":{"metric":"temperature","op":">","value":30.0}}]'
    fake_result = MagicMock()
    fake_result.stdout = new_rules
    monkeypatch.setattr(weather.subprocess, 'run', lambda *a, **k: fake_result)

    reload_flag = tmp_path / 'reload'
    monkeypatch.setattr(weather, 'RELOAD_FLAG', str(reload_flag))

    weather._pull_rules_from_mqtt()

    assert rules_file.read_text() == new_rules
    assert reload_flag.exists()


def test_pull_rules_from_mqtt_ignores_empty_or_invalid_payload(monkeypatch, tmp_path):
    rules_file = tmp_path / 'rules.json'
    rules_file.write_text('[]')
    monkeypatch.setattr(weather, 'RULES_CFG', str(rules_file))

    fake_result = MagicMock()
    fake_result.stdout = 'not valid json'
    monkeypatch.setattr(weather.subprocess, 'run', lambda *a, **k: fake_result)

    weather._pull_rules_from_mqtt()

    assert rules_file.read_text() == '[]'  # unchanged — invalid payload ignored


def test_pull_rules_from_mqtt_noop_on_no_message(monkeypatch, tmp_path):
    rules_file = tmp_path / 'rules.json'
    rules_file.write_text('[]')
    monkeypatch.setattr(weather, 'RULES_CFG', str(rules_file))

    fake_result = MagicMock()
    fake_result.stdout = ''
    monkeypatch.setattr(weather.subprocess, 'run', lambda *a, **k: fake_result)

    weather._pull_rules_from_mqtt()

    assert rules_file.read_text() == '[]'
```

Add `from unittest.mock import MagicMock` to the file's imports if not already present (it is, from the earlier FCM push work in this same file — check before adding a duplicate import).

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd pi && py -3 -m pytest tests/test_weather_rules.py -v`
Expected: FAIL — `AttributeError: module 'weather' has no attribute '_pull_rules_from_mqtt'`.

- [ ] **Step 7: Implement `_pull_rules_from_mqtt()`**

In `pi/scripts/weather.py`, immediately after the existing `_pull_location_from_mqtt()` function (before `def load_location()`), add:

```python
def _pull_rules_from_mqtt():
    """Check for a retained rules/update message published by the app.

    Mirrors _pull_location_from_mqtt() exactly: rules/update is retained
    (as of this fix — see greenhouse_repository.dart::publishRules), so
    polling briefly on every cycle reliably catches the last-published
    value regardless of exact timing.
    """
    import subprocess
    try:
        result = subprocess.run(
            ['mosquitto_sub', '-h', MQTT_HOST, '-p', MQTT_PORT,
             '-t', 'greenhouse/rules/update', '-C', '1', '-W', '2'],
            capture_output=True, text=True, timeout=5,
        )
        msg = result.stdout.strip()
        if not msg:
            return
        json.loads(msg)  # validate before writing — a malformed payload must not corrupt rules.json
        with open(RULES_CFG, 'w') as f:
            f.write(msg)
        open(RELOAD_FLAG, 'w').close()
        print('[weather] Rules updated from app', flush=True)
    except Exception as e:
        print(f'[weather] WARN: rules pull: {e}', flush=True)
```

Then in `run()`, change:

```python
    _pull_location_from_mqtt()
    publish_rules()

    while _running:
        # Pick up location/interval changes published from the app
        _pull_location_from_mqtt()
```

to:

```python
    _pull_location_from_mqtt()
    _pull_rules_from_mqtt()
    publish_rules()

    while _running:
        # Pick up location/interval changes published from the app
        _pull_location_from_mqtt()
        # Pick up rule changes published from the app (retained, so this
        # reliably catches the last update regardless of exact timing)
        _pull_rules_from_mqtt()
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd pi && py -3 -m pytest tests/test_weather_rules.py -v`
Expected: all tests pass (existing + 3 new).

Run: `cd pi && py -3 -m pytest -v` (full suite, check no regressions)
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add app/lib/repository/greenhouse_repository.dart app/test/repository/greenhouse_repository_test.dart pi/scripts/weather.py pi/tests/test_weather_rules.py
git commit -m "fix: rule edits from the app now actually reach the Pi"
```

---

### Task 2: Optional action + notify field in the rule engine

**Files:**
- Modify: `pi/scripts/weather.py` (`_fire()` inside `eval_rules()`)
- Test: `pi/tests/test_weather_rules.py`

**Interfaces:**
- Consumes: `push.send_push(title: str, body: str) -> None` (existing, from the FCM feature).
- Produces: `_fire()` no longer requires `rule['action']` to exist, and only calls `send_push()` when `rule.get('notify', True)` is truthy — used by Task 4's seeded alert-only rules.

- [ ] **Step 1: Write the failing tests**

Add `import time` and `import sqlite3` to `pi/tests/test_weather_rules.py`'s existing import block if not already present (check first — `sqlite3` may already be imported for the duration-rule tests further down the file).

Add to `pi/tests/test_weather_rules.py`:

```python
def test_eval_rules_alert_only_rule_does_not_publish_actuator_command(monkeypatch):
    published = []
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: published.append(a))
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    rule = {
        'id': 'zone1-dry', 'name': 'Zone 1 soil dry', 'enabled': True,
        'trigger': {'metric': 'zone1/soil_moisture', 'op': '<', 'value': 15.0, 'duration_minutes': 5},
    }
    # duration rules need the recorder DB; use a real one seeded to fire.
    # eval_rules() computes `now` internally as int(time.time()) (it takes
    # no `now` parameter, unlike eval_duration_rule's own direct tests
    # elsewhere in this file) — seed data relative to real current time,
    # not a fixed fake epoch, or the cutoff filter will never match it.
    with tempfile.TemporaryDirectory() as d:
        conn = recorder.init_db(os.path.join(d, 'test.db'))
        series_ids = {}
        now = int(time.time())
        recorder.write_buckets(conn, series_ids, [
            (('zone', 'zone1', 'soil_moisture'), now - 240, 10.0, 10.0, 10.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 180, 10.0, 10.0, 10.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 120, 10.0, 10.0, 10.0, 1),
            (('zone', 'zone1', 'soil_moisture'), now - 60,  10.0, 10.0, 10.0, 1),
        ])
        conn.close()
        monkeypatch.setattr(weather, 'RECORDER_DB', os.path.join(d, 'test.db'))
        conn2 = sqlite3.connect(os.path.join(d, 'test.db'))
        monkeypatch.setattr(weather.sqlite3, 'connect', lambda *a, **k: conn2)
        weather.eval_rules([rule], {})

    assert len(pushed) == 1
    assert pushed[0][0] == 'Zone 1 soil dry'
    # No actuator command published — only the mqtt alert, no `greenhouse/actuators/...` topic
    actuator_topics = [p[0] for p in published if p and str(p[0]).startswith('greenhouse/actuators/')]
    assert actuator_topics == []


def test_eval_rules_notify_false_skips_push_but_still_publishes_alert(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    rule = {
        'id': 'r1', 'name': 'Silent Rule', 'enabled': True, 'notify': False,
        'trigger': {'metric': 'temperature', 'op': '>', 'value': 30.0},
        'action': {'actuator': 'fan1', 'command': 'ON'},
    }
    weather.eval_rules([rule], {'temperature': 35.0})

    assert pushed == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && py -3 -m pytest tests/test_weather_rules.py -v`
Expected: FAIL on `test_eval_rules_alert_only_rule_does_not_publish_actuator_command` with a `KeyError: 'action'` (current `_fire()` unconditionally reads `rule['action']`), and FAIL on `test_eval_rules_notify_false_skips_push_but_still_publishes_alert` with `pushed` containing an entry (current code always calls `send_push`).

- [ ] **Step 3: Implement the fix**

In `pi/scripts/weather.py`, change `_fire()` from:

```python
    def _fire(rule, message):
        action   = rule['action']
        actuator = action['actuator']
        command  = action['command']
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
        send_push(rule.get('name', 'Rule'), message)
```

to:

```python
    def _fire(rule, message):
        action = rule.get('action')
        if action:
            actuator = action['actuator']
            command  = action['command']
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && py -3 -m pytest tests/test_weather_rules.py -v`
Expected: all pass.

Run: `cd pi && py -3 -m pytest -v` (full suite)
Expected: all pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/weather.py pi/tests/test_weather_rules.py
git commit -m "feat: rules support alert-only mode and a notify toggle"
```

---

### Task 3: Built-in alert settings (frost forecast, daily summary toggles)

**Files:**
- Modify: `pi/scripts/weather.py` (new `_pull_notification_settings()`, `load_notification_settings()`, `publish_notification_settings()`; wire into `maybe_send_frost_alert`/`maybe_send_daily_summary`/`run()`)
- Test: `pi/tests/test_weather_rules.py`

**Interfaces:**
- Produces: `load_notification_settings() -> dict` (keys `frost_forecast`, `daily_summary`, both defaulting to `True`), used by `maybe_send_frost_alert`/`maybe_send_daily_summary`.

- [ ] **Step 1: Write the failing tests**

Add to `pi/tests/test_weather_rules.py`:

```python
def test_load_notification_settings_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, 'NOTIFICATION_SETTINGS_CFG', str(tmp_path / 'missing.json'))
    settings = weather.load_notification_settings()
    assert settings == {'frost_forecast': True, 'daily_summary': True}


def test_load_notification_settings_reads_file(tmp_path, monkeypatch):
    cfg = tmp_path / 'notification_settings.json'
    cfg.write_text('{"frost_forecast": false, "daily_summary": true}')
    monkeypatch.setattr(weather, 'NOTIFICATION_SETTINGS_CFG', str(cfg))
    settings = weather.load_notification_settings()
    assert settings == {'frost_forecast': False, 'daily_summary': True}


def test_maybe_send_frost_alert_respects_frost_forecast_off(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    monkeypatch.setattr(weather, '_last_frost_alert', None)
    monkeypatch.setattr(weather, 'load_notification_settings',
                         lambda: {'frost_forecast': False, 'daily_summary': True})
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    data = {'hourly': {'temperature_2m': [-2.0] * 12}}
    weather.maybe_send_frost_alert(data)

    assert pushed == []  # push suppressed
    # mqtt alert still fires — verified indirectly: _last_frost_alert still gets set
    assert weather._last_frost_alert is not None


def test_maybe_send_daily_summary_respects_daily_summary_off(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    monkeypatch.setattr(weather, '_last_summary_date', None)
    monkeypatch.setattr(weather, 'load_notification_settings',
                         lambda: {'frost_forecast': True, 'daily_summary': False})

    class _FrozenClock:
        @staticmethod
        def now():
            return datetime(2026, 7, 10, 7, 0, 0)
    monkeypatch.setattr(weather, 'datetime', _FrozenClock)

    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    data = {'hourly': {'temperature_2m': [20.0] * 24, 'precipitation': [0.0] * 24}}
    weather.maybe_send_daily_summary(data, {'temperature': 22.0, 'wind_kmh': 5.0})

    assert pushed == []
    assert weather._last_summary_date is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && py -3 -m pytest tests/test_weather_rules.py -v`
Expected: FAIL — `AttributeError: module 'weather' has no attribute 'NOTIFICATION_SETTINGS_CFG'` (and `load_notification_settings`).

- [ ] **Step 3: Implement**

In `pi/scripts/weather.py`, add to the "Config paths" section near the top (after `RULES_CFG`):

```python
NOTIFICATION_SETTINGS_CFG = '/etc/greenhouse/notification_settings.json'
```

After `_pull_rules_from_mqtt()` (from Task 1), add:

```python
def _pull_notification_settings():
    """Check for a retained settings/notifications message published by the app."""
    import subprocess
    try:
        result = subprocess.run(
            ['mosquitto_sub', '-h', MQTT_HOST, '-p', MQTT_PORT,
             '-t', 'greenhouse/settings/notifications', '-C', '1', '-W', '2'],
            capture_output=True, text=True, timeout=5,
        )
        msg = result.stdout.strip()
        if not msg:
            return
        data = json.loads(msg)
        settings = {
            'frost_forecast': bool(data.get('frost_forecast', True)),
            'daily_summary':  bool(data.get('daily_summary', True)),
        }
        with open(NOTIFICATION_SETTINGS_CFG, 'w') as f:
            json.dump(settings, f)
    except Exception as e:
        print(f'[weather] WARN: notification settings pull: {e}', flush=True)


def load_notification_settings() -> dict:
    try:
        with open(NOTIFICATION_SETTINGS_CFG) as f:
            d = json.load(f)
        return {
            'frost_forecast': bool(d.get('frost_forecast', True)),
            'daily_summary':  bool(d.get('daily_summary', True)),
        }
    except Exception:
        return {'frost_forecast': True, 'daily_summary': True}


def publish_notification_settings():
    """Publish current notification settings as retained so app gets them on connect."""
    settings = load_notification_settings()
    mqtt_publish('greenhouse/settings/notifications/current', json.dumps(settings), retain=True)
```

Modify `maybe_send_frost_alert()` — change:

```python
        if min_t < 0:
            alert = {
                'type': 'frost',
                'message': f'Frost expected tonight ({min_t:.1f} °C). Frost protection activated.',
                'severity': 'warning',
            }
            mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
            send_push('Frost warning', alert['message'])
            _last_frost_alert = today
            print(f'[weather] Frost alert sent: min_t={min_t}', flush=True)
```

to:

```python
        if min_t < 0:
            alert = {
                'type': 'frost',
                'message': f'Frost expected tonight ({min_t:.1f} °C). Frost protection activated.',
                'severity': 'warning',
            }
            mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
            if load_notification_settings().get('frost_forecast', True):
                send_push('Frost warning', alert['message'])
            _last_frost_alert = today
            print(f'[weather] Frost alert sent: min_t={min_t}', flush=True)
```

Modify `maybe_send_daily_summary()` — change:

```python
        mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
        send_push("Today's forecast", summary_msg)
        _last_summary_date = today
        print(f'[weather] Daily summary sent: {summary_msg}', flush=True)
```

to:

```python
        mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
        if load_notification_settings().get('daily_summary', True):
            send_push("Today's forecast", summary_msg)
        _last_summary_date = today
        print(f'[weather] Daily summary sent: {summary_msg}', flush=True)
```

Finally, wire the poller + startup publish into `run()` — change:

```python
    _pull_location_from_mqtt()
    _pull_rules_from_mqtt()
    publish_rules()

    while _running:
        # Pick up location/interval changes published from the app
        _pull_location_from_mqtt()
        # Pick up rule changes published from the app (retained, so this
        # reliably catches the last update regardless of exact timing)
        _pull_rules_from_mqtt()
```

to:

```python
    _pull_location_from_mqtt()
    _pull_rules_from_mqtt()
    _pull_notification_settings()
    publish_rules()
    publish_notification_settings()

    while _running:
        # Pick up location/interval changes published from the app
        _pull_location_from_mqtt()
        # Pick up rule changes published from the app (retained, so this
        # reliably catches the last update regardless of exact timing)
        _pull_rules_from_mqtt()
        # Pick up notification-settings changes published from the app
        _pull_notification_settings()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && py -3 -m pytest tests/test_weather_rules.py -v`
Expected: all pass.

Run: `cd pi && py -3 -m pytest -v` (full suite)
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/weather.py pi/tests/test_weather_rules.py
git commit -m "feat: add on/off settings for the two built-in system alerts"
```

---

### Task 4: Seed default rules and notification settings in install.sh

**Files:**
- Modify: `pi/install.sh` (rules.json seed block, new notification_settings.json seed block)

**Interfaces:**
- None (shell script config seeding; no interface consumed/produced by other tasks — the new rules/settings are read by code from Tasks 2-3, already merged by the time this runs on a real Pi).

- [ ] **Step 1: Check current syntax baseline**

Run: `bash -n pi/install.sh`
Expected: no output.

- [ ] **Step 2: Extend the default rules seed with the six per-zone dry/humid rules**

In `pi/install.sh`, change:

```bash
echo "==> Writing default automation rules config..."
[ -f /etc/greenhouse/rules.json ] || cat > /etc/greenhouse/rules.json << 'EOF'
[
  {"id":"rain-close","name":"Close fan on rain","enabled":true,
   "trigger":{"metric":"rain_mm_1h","op":">","value":0.3},
   "action":{"actuator":"fan1","command":"OFF"}},
  {"id":"frost-heat","name":"Frost protection","enabled":true,
   "trigger":{"metric":"temperature","op":"<","value":3},
   "action":{"actuator":"pump1","command":"ON"}},
  {"id":"heat-fan","name":"Heat wave ventilation","enabled":true,
   "trigger":{"metric":"temperature","op":">","value":35},
   "action":{"actuator":"fan1","command":"ON"}}
]
EOF
```

to:

```bash
echo "==> Writing default automation rules config..."
# The zone1/2/3 dry+humid rules are alert-only (no "action" key) — they're
# a starting point using the specific numbers requested for this farm
# (soil < 15% for 2 days, humidity > 70% for 24h), fully editable/deletable
# via the app's rule builder like any other rule.
[ -f /etc/greenhouse/rules.json ] || cat > /etc/greenhouse/rules.json << 'EOF'
[
  {"id":"rain-close","name":"Close fan on rain","enabled":true,
   "trigger":{"metric":"rain_mm_1h","op":">","value":0.3},
   "action":{"actuator":"fan1","command":"OFF"}},
  {"id":"frost-heat","name":"Frost protection","enabled":true,
   "trigger":{"metric":"temperature","op":"<","value":3},
   "action":{"actuator":"pump1","command":"ON"}},
  {"id":"heat-fan","name":"Heat wave ventilation","enabled":true,
   "trigger":{"metric":"temperature","op":">","value":35},
   "action":{"actuator":"fan1","command":"ON"}},
  {"id":"zone1-dry","name":"Zone 1 soil dry","enabled":true,"notify":true,
   "trigger":{"metric":"zone1/soil_moisture","op":"<","value":15,"duration_minutes":2880}},
  {"id":"zone2-dry","name":"Zone 2 soil dry","enabled":true,"notify":true,
   "trigger":{"metric":"zone2/soil_moisture","op":"<","value":15,"duration_minutes":2880}},
  {"id":"zone3-dry","name":"Zone 3 soil dry","enabled":true,"notify":true,
   "trigger":{"metric":"zone3/soil_moisture","op":"<","value":15,"duration_minutes":2880}},
  {"id":"zone1-humid","name":"Zone 1 too humid","enabled":true,"notify":true,
   "trigger":{"metric":"zone1/air_humidity","op":">","value":70,"duration_minutes":1440}},
  {"id":"zone2-humid","name":"Zone 2 too humid","enabled":true,"notify":true,
   "trigger":{"metric":"zone2/air_humidity","op":">","value":70,"duration_minutes":1440}},
  {"id":"zone3-humid","name":"Zone 3 too humid","enabled":true,"notify":true,
   "trigger":{"metric":"zone3/air_humidity","op":">","value":70,"duration_minutes":1440}}
]
EOF
```

- [ ] **Step 3: Add the notification_settings.json seed**

In `pi/install.sh`, immediately after that rules.json block (before the `echo "==> Writing default recorder config..."` line), add:

```bash
echo "==> Writing default notification settings..."
[ -f /etc/greenhouse/notification_settings.json ] || cat > /etc/greenhouse/notification_settings.json << 'EOF'
{"frost_forecast": true, "daily_summary": true}
EOF
```

- [ ] **Step 4: Verify syntax**

Run: `bash -n pi/install.sh`
Expected: no output.

- [ ] **Step 5: Verify the new content is present**

Run: `grep -c "zone1-dry\|zone2-dry\|zone3-dry\|zone1-humid\|zone2-humid\|zone3-humid" pi/install.sh`
Expected: `6`

Run: `grep -c "notification_settings.json" pi/install.sh`
Expected: `2` (the `[ -f ... ]` check line and the `cat >` line)

- [ ] **Step 6: Commit**

```bash
git add pi/install.sh
git commit -m "feat: seed default dry/humid rules and notification settings"
```

---

### Task 5: WeatherRule model rewrite (zone/metric split, optional action, duration, notify)

**Files:**
- Modify: `app/lib/models/weather_rule.dart`
- Test: `app/test/models/weather_rule_test.dart` (new file)

**Interfaces:**
- Produces: `WeatherRule` with fields `id, name, enabled, notify, zone (String?), metric, op, value, durationMinutes (int?), actuatorId (String?), command (String?)`; `zoneLabel` getter; `metricLabel` getter (extended for zone metrics) — used by Tasks 7-8's rule form and rule cards.

- [ ] **Step 1: Write the failing tests**

Create `app/test/models/weather_rule_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/weather_rule.dart';

void main() {
  test('fromJson parses a weather-level rule with an action', () {
    final rule = WeatherRule.fromJson({
      'id': 'heat-fan', 'name': 'Heat wave ventilation', 'enabled': true,
      'trigger': {'metric': 'temperature', 'op': '>', 'value': 35},
      'action': {'actuator': 'fan1', 'command': 'ON'},
    });

    expect(rule.zone, isNull);
    expect(rule.metric, 'temperature');
    expect(rule.op, '>');
    expect(rule.value, 35.0);
    expect(rule.durationMinutes, isNull);
    expect(rule.actuatorId, 'fan1');
    expect(rule.command, 'ON');
    expect(rule.notify, true); // defaulted
  });

  test('fromJson parses a zone-prefixed metric and splits zone/metric apart', () {
    final rule = WeatherRule.fromJson({
      'id': 'zone1-dry', 'name': 'Zone 1 soil dry', 'enabled': true, 'notify': true,
      'trigger': {'metric': 'zone1/soil_moisture', 'op': '<', 'value': 15, 'duration_minutes': 2880},
    });

    expect(rule.zone, 'zone1');
    expect(rule.metric, 'soil_moisture');
    expect(rule.durationMinutes, 2880);
    expect(rule.actuatorId, isNull);
    expect(rule.command, isNull);
  });

  test('fromJson defaults notify to true when absent', () {
    final rule = WeatherRule.fromJson({
      'id': 'r1', 'name': 'R', 'enabled': true,
      'trigger': {'metric': 'temperature', 'op': '>', 'value': 1},
      'action': {'actuator': 'a', 'command': 'ON'},
    });
    expect(rule.notify, true);
  });

  test('fromJson respects notify: false', () {
    final rule = WeatherRule.fromJson({
      'id': 'r1', 'name': 'R', 'enabled': true, 'notify': false,
      'trigger': {'metric': 'temperature', 'op': '>', 'value': 1},
      'action': {'actuator': 'a', 'command': 'ON'},
    });
    expect(rule.notify, false);
  });

  test('toJson composes zone/metric back into a single wire-format string', () {
    const rule = WeatherRule(
      id: 'zone2-humid', name: 'Zone 2 too humid', enabled: true, notify: true,
      zone: 'zone2', metric: 'air_humidity', op: '>', value: 70.0,
      durationMinutes: 1440, actuatorId: null, command: null,
    );

    final json = rule.toJson();
    expect((json['trigger'] as Map)['metric'], 'zone2/air_humidity');
    expect((json['trigger'] as Map)['duration_minutes'], 1440);
    expect(json.containsKey('action'), false); // alert-only — no action key at all
  });

  test('toJson omits duration_minutes when null', () {
    const rule = WeatherRule(
      id: 'r1', name: 'R', enabled: true, notify: true,
      zone: null, metric: 'temperature', op: '>', value: 35.0,
      durationMinutes: null, actuatorId: 'fan1', command: 'ON',
    );

    final json = rule.toJson();
    expect((json['trigger'] as Map).containsKey('duration_minutes'), false);
    expect(json['action'], {'actuator': 'fan1', 'command': 'ON'});
  });

  test('round-trips through toJson/fromJson unchanged', () {
    const rule = WeatherRule(
      id: 'zone1-dry', name: 'Zone 1 soil dry', enabled: true, notify: false,
      zone: 'zone1', metric: 'soil_moisture', op: '<', value: 15.0,
      durationMinutes: 2880, actuatorId: null, command: null,
    );

    final roundTripped = WeatherRule.fromJson(rule.toJson());
    expect(roundTripped.toJson(), rule.toJson());
  });

  test('zoneLabel formats zone names, or "Weather" for null', () {
    const zoneRule = WeatherRule(
      id: 'r', name: 'n', enabled: true, notify: true,
      zone: 'zone3', metric: 'soil_moisture', op: '<', value: 1.0,
      durationMinutes: 60, actuatorId: null, command: null,
    );
    const weatherRule = WeatherRule(
      id: 'r', name: 'n', enabled: true, notify: true,
      zone: null, metric: 'temperature', op: '<', value: 1.0,
      durationMinutes: null, actuatorId: null, command: null,
    );
    expect(zoneRule.zoneLabel, 'Zone 3');
    expect(weatherRule.zoneLabel, 'Weather');
  });

  test('metricLabel describes zone metrics distinctly from weather metrics', () {
    const soilRule = WeatherRule(
      id: 'r', name: 'n', enabled: true, notify: true,
      zone: 'zone1', metric: 'soil_moisture', op: '<', value: 1.0,
      durationMinutes: 60, actuatorId: null, command: null,
    );
    expect(soilRule.metricLabel, 'Soil moisture (%)');
  });
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/models/weather_rule_test.dart`
Expected: FAIL — compile errors (current `WeatherRule` constructor doesn't have `zone`/`durationMinutes` parameters, `metric` doesn't exist, etc.)

- [ ] **Step 3: Rewrite the model**

Replace the full contents of `app/lib/models/weather_rule.dart`:

```dart
import 'dart:convert';

class WeatherRule {
  final String id;
  final String name;
  final bool enabled;
  final bool notify;
  final String? zone; // null = weather-level (ambient/forecast) metric
  final String metric; // bare metric name, e.g. "soil_moisture" or "temperature"
  final String op;
  final double value;
  final int? durationMinutes; // required by the backend whenever zone != null
  final String? actuatorId; // null together with command = alert-only rule
  final String? command;

  const WeatherRule({
    required this.id,
    required this.name,
    required this.enabled,
    required this.notify,
    required this.zone,
    required this.metric,
    required this.op,
    required this.value,
    required this.durationMinutes,
    required this.actuatorId,
    required this.command,
  });

  factory WeatherRule.fromJson(Map<String, dynamic> json) {
    final trigger = json['trigger'] as Map;
    final rawMetric = trigger['metric'] as String? ?? '';
    final parts = rawMetric.split('/');
    final zone = parts.length == 2 ? parts[0] : null;
    final metric = parts.length == 2 ? parts[1] : rawMetric;
    final action = json['action'] as Map?;
    return WeatherRule(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? '',
      enabled: json['enabled'] as bool? ?? true,
      notify: json['notify'] as bool? ?? true,
      zone: zone,
      metric: metric,
      op: trigger['op'] as String? ?? '>',
      value: (trigger['value'] as num).toDouble(),
      durationMinutes: (trigger['duration_minutes'] as num?)?.toInt(),
      actuatorId: action?['actuator'] as String?,
      command: action?['command'] as String?,
    );
  }

  Map<String, dynamic> toJson() {
    final trigger = <String, dynamic>{
      'metric': zone != null ? '$zone/$metric' : metric,
      'op': op,
      'value': value,
    };
    if (durationMinutes != null) trigger['duration_minutes'] = durationMinutes;
    final json = <String, dynamic>{
      'id': id,
      'name': name,
      'enabled': enabled,
      'notify': notify,
      'trigger': trigger,
    };
    if (actuatorId != null && command != null) {
      json['action'] = {'actuator': actuatorId, 'command': command};
    }
    return json;
  }

  WeatherRule copyWith({
    String? name,
    bool? enabled,
    bool? notify,
    double? value,
  }) =>
      WeatherRule(
        id: id,
        name: name ?? this.name,
        enabled: enabled ?? this.enabled,
        notify: notify ?? this.notify,
        zone: zone,
        metric: metric,
        op: op,
        value: value ?? this.value,
        durationMinutes: durationMinutes,
        actuatorId: actuatorId,
        command: command,
      );

  static List<WeatherRule> listFromJson(String payload) {
    final list = jsonDecode(payload) as List;
    return list.map((e) => WeatherRule.fromJson(e as Map<String, dynamic>)).toList();
  }

  static String listToJson(List<WeatherRule> rules) =>
      jsonEncode(rules.map((r) => r.toJson()).toList());

  String get conditionLabel => '$metricLabel $op $value';

  String get zoneLabel => zone == null ? 'Weather' : 'Zone ${zone!.replaceFirst('zone', '')}';

  String get metricLabel {
    if (zone != null) {
      switch (metric) {
        case 'soil_moisture':   return 'Soil moisture (%)';
        case 'air_humidity':    return 'Air humidity (%)';
        case 'air_temperature': return 'Air temperature (°C)';
        default:                return metric;
      }
    }
    switch (metric) {
      case 'temperature': return 'Temperature (°C)';
      case 'rain_mm_1h':  return 'Rain next hour (mm)';
      case 'humidity':    return 'Humidity (%)';
      case 'wind_kmh':    return 'Wind (km/h)';
      case 'uv_index':    return 'UV Index';
      default:            return metric;
    }
  }
}
```

- [ ] **Step 4: Fix the one existing call site that used the old constructor/fields**

`app/lib/screens/weather/weather_screen.dart`'s `_RuleCard._ruleIcon(rule.triggerMetric)` and the `_RuleCard` subtitle referencing `rule.actuatorId`/`rule.command` as non-nullable will now fail to compile — **do not fix this here**; Task 8 rewrites `_RuleCard` fully. For this task only, confirm the *model* compiles and its own test passes; a broader `flutter analyze` failure from `weather_screen.dart` at this point is expected and resolved by Task 8, not this one.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd app && flutter test test/models/weather_rule_test.dart`
Expected: `8 passed` (all tests listed above).

- [ ] **Step 6: Commit**

```bash
git add app/lib/models/weather_rule.dart app/test/models/weather_rule_test.dart
git commit -m "feat: rewrite WeatherRule model — zone/metric split, optional action, duration, notify"
```

Note: `flutter analyze`/`flutter test` for the whole project will show errors in `weather_screen.dart` until Task 8 lands — this is expected and does not need fixing in this task or Tasks 6-7.

---

### Task 6: Notification settings repository/provider wiring

**Files:**
- Create: `app/lib/models/notification_settings.dart`
- Modify: `app/lib/models/weather_events.dart` (add `NotificationSettingsRaw`)
- Modify: `app/lib/connection/mqtt_connection.dart` (routing for the new topic)
- Modify: `app/lib/repository/greenhouse_repository.dart` (stream + publish method)
- Modify: `app/lib/providers/connection_provider.dart` (new provider)
- Test: `app/test/repository/greenhouse_repository_test.dart`

**Interfaces:**
- Produces: `NotificationSettings(frostForecast, dailySummary)` model; `GreenhouseRepository.notificationSettings` (`Stream<NotificationSettings>`, cached-then-live like `rules`/`forecast`); `GreenhouseRepository.publishNotificationSettings(NotificationSettings)`; `notificationSettingsProvider` — used by Task 8's Rules tab.

- [ ] **Step 1: Create the model**

Create `app/lib/models/notification_settings.dart`:

```dart
class NotificationSettings {
  final bool frostForecast;
  final bool dailySummary;

  const NotificationSettings({required this.frostForecast, required this.dailySummary});

  factory NotificationSettings.fromJson(Map<String, dynamic> json) => NotificationSettings(
        frostForecast: json['frost_forecast'] as bool? ?? true,
        dailySummary: json['daily_summary'] as bool? ?? true,
      );

  Map<String, dynamic> toJson() => {
        'frost_forecast': frostForecast,
        'daily_summary': dailySummary,
      };

  NotificationSettings copyWith({bool? frostForecast, bool? dailySummary}) => NotificationSettings(
        frostForecast: frostForecast ?? this.frostForecast,
        dailySummary: dailySummary ?? this.dailySummary,
      );
}
```

- [ ] **Step 2: Add the raw event wrapper**

In `app/lib/models/weather_events.dart`, add after `RulesPayloadRaw`:

```dart
/// Carries the raw JSON payload from greenhouse/settings/notifications/current.
class NotificationSettingsRaw {
  final String payload;
  const NotificationSettingsRaw(this.payload);
}
```

- [ ] **Step 3: Wire MQTT routing**

In `app/lib/connection/mqtt_connection.dart`, add to `_route()`:

```dart
    } else if (isRulesCurrentTopic(topic)) {
      _events.add(RulesPayloadRaw(payload));
    } else if (isNotificationSettingsTopic(topic)) {
      _events.add(NotificationSettingsRaw(payload));
    } else if (isHistoryResponseTopic(topic)) {
```

(This inserts the new `else if` branch between the existing `isRulesCurrentTopic` and `isHistoryResponseTopic` branches — do not reorder the others.)

Add the static predicate alongside `isRulesCurrentTopic`:

```dart
  static bool isRulesCurrentTopic(String t) => t == 'greenhouse/rules/current';
  static bool isNotificationSettingsTopic(String t) => t == 'greenhouse/settings/notifications/current';
```

- [ ] **Step 4: Write the failing repository test**

Add to `app/test/repository/greenhouse_repository_test.dart`:

```dart
  test('publishNotificationSettings retains the message', () async {
    await repo.publishNotificationSettings(
      const NotificationSettings(frostForecast: false, dailySummary: true),
    );

    verify(() => conn.publishRaw(
          'greenhouse/settings/notifications',
          '{"frost_forecast":false,"daily_summary":true}',
          retain: true,
        )).called(1);
  });

  test('notificationSettings stream emits parsed settings from a NotificationSettingsRaw event', () async {
    final future = repo.notificationSettings.first;
    eventsCtrl.add(const NotificationSettingsRaw('{"frost_forecast":false,"daily_summary":true}'));
    final settings = await future;
    expect(settings.frostForecast, false);
    expect(settings.dailySummary, true);
  });
```

Add the import `import 'package:greenhouse_app/models/notification_settings.dart';` and
`import 'package:greenhouse_app/models/weather_events.dart';` to the test file if not already present (`weather_events.dart` isn't currently imported there — check before adding a duplicate).

- [ ] **Step 5: Run test to verify it fails**

Run: `cd app && flutter test test/repository/greenhouse_repository_test.dart`
Expected: FAIL — compile error, `publishNotificationSettings`/`notificationSettings` don't exist on `GreenhouseRepository` yet.

- [ ] **Step 6: Implement in the repository**

In `app/lib/repository/greenhouse_repository.dart`, add the import:

```dart
import 'package:greenhouse_app/models/notification_settings.dart';
```

Add a controller and cached value alongside the existing `_rulesCtrl`/`_rules` fields:

```dart
  final _notificationSettingsCtrl = StreamController<NotificationSettings>.broadcast();
  NotificationSettings? _notificationSettings;
```

Add a cached-then-live getter alongside the existing `rules` getter:

```dart
  /// Fires when notification settings are received / updated.
  Stream<NotificationSettings> get notificationSettings async* {
    if (_notificationSettings != null) yield _notificationSettings!;
    yield* _notificationSettingsCtrl.stream;
  }
```

In `_handle()`, add a branch alongside the existing `else if (event is RulesPayloadRaw)`:

```dart
    } else if (event is NotificationSettingsRaw) {
      try {
        final settings = NotificationSettings.fromJson(jsonDecode(event.payload) as Map<String, dynamic>);
        _notificationSettings = settings;
        _notificationSettingsCtrl.add(settings);
      } catch (_) {}
    }
```

Add the publish method alongside the existing `publishRules`:

```dart
  /// Push notification-preference settings to the Pi (retained, matching
  /// publishRules/publishLocation's retained-for-reliable-polling pattern).
  Future<void> publishNotificationSettings(NotificationSettings settings) async {
    _notificationSettings = settings;
    _notificationSettingsCtrl.add(settings);
    await connection.publishRaw(
      'greenhouse/settings/notifications',
      jsonEncode(settings.toJson()),
      retain: true,
    );
  }
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd app && flutter test test/repository/greenhouse_repository_test.dart`
Expected: all tests in the file pass.

- [ ] **Step 8: Add the provider**

In `app/lib/providers/connection_provider.dart`, add the import:

```dart
import 'package:greenhouse_app/models/notification_settings.dart';
```

Add, alongside the existing `rulesProvider`:

```dart
/// Emits the current notification-preference settings from the Pi.
final notificationSettingsProvider = StreamProvider<NotificationSettings>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).notificationSettings;
});
```

- [ ] **Step 9: Run the whole app test suite to check for regressions in files this task touched**

Run: `cd app && flutter test test/repository/ test/connection/`
Expected: all pass. (Do not run the full `flutter test` yet — `weather_screen.dart` still won't compile until Task 8, per Task 5's note.)

- [ ] **Step 10: Commit**

```bash
git add app/lib/models/notification_settings.dart app/lib/models/weather_events.dart \
        app/lib/connection/mqtt_connection.dart app/lib/repository/greenhouse_repository.dart \
        app/lib/providers/connection_provider.dart app/test/repository/greenhouse_repository_test.dart
git commit -m "feat: sync built-in alert on/off settings between app and Pi"
```

---

### Task 7: Rule builder dialog (new file)

**Files:**
- Create: `app/lib/screens/weather/rule_form_dialog.dart`
- Test: `app/test/widgets/rule_form_dialog_test.dart` (new file)

**Interfaces:**
- Consumes: `WeatherRule` (Task 5).
- Produces: `Future<WeatherRule?> showRuleFormDialog(BuildContext context, {WeatherRule? existing, required List<String> zones, required List<String> actuatorIds})` — used by Task 8's Rules tab for both "Add rule" and "Edit rule".

- [ ] **Step 1: Write the failing widget tests**

Create `app/test/widgets/rule_form_dialog_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/weather_rule.dart';
import 'package:greenhouse_app/screens/weather/rule_form_dialog.dart';

void main() {
  Future<WeatherRule?> openDialog(WidgetTester tester, {WeatherRule? existing}) async {
    WeatherRule? result;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(builder: (context) {
          return ElevatedButton(
            onPressed: () async {
              result = await showRuleFormDialog(
                context,
                existing: existing,
                zones: const ['zone1', 'zone2'],
                actuatorIds: const ['fan1', 'pump1'],
              );
            },
            child: const Text('Open'),
          );
        }),
      ),
    ));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    return result;
  }

  testWidgets('selecting a zone requires a duration before Save is enabled', (tester) async {
    await openDialog(tester);

    await tester.enterText(find.widgetWithText(TextField, 'Name'), 'Zone 1 dry');
    await tester.tap(find.byKey(const Key('rule-form-zone-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Zone 1').last);
    await tester.pumpAndSettle();

    // Duration field appears and is required once a zone is selected.
    expect(find.widgetWithText(TextField, 'Duration (minutes)'), findsOneWidget);

    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    // Dialog is still open — Save was a no-op because duration is empty.
    expect(find.text('Save'), findsOneWidget);
  });

  testWidgets('creating an alert-only zone rule returns the expected WeatherRule', (tester) async {
    WeatherRule? result;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(builder: (context) {
          return ElevatedButton(
            onPressed: () async {
              result = await showRuleFormDialog(
                context,
                zones: const ['zone1', 'zone2'],
                actuatorIds: const ['fan1', 'pump1'],
              );
            },
            child: const Text('Open'),
          );
        }),
      ),
    ));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();

    await tester.enterText(find.widgetWithText(TextField, 'Name'), 'Zone 1 dry');
    await tester.tap(find.byKey(const Key('rule-form-zone-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Zone 1').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('rule-form-metric-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Soil moisture (%)').last);
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Threshold value'), '15');
    await tester.enterText(find.widgetWithText(TextField, 'Duration (minutes)'), '2880');

    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();

    expect(result, isNotNull);
    expect(result!.zone, 'zone1');
    expect(result!.metric, 'soil_moisture');
    expect(result!.value, 15.0);
    expect(result!.durationMinutes, 2880);
    expect(result!.actuatorId, isNull); // "Also control a device" left off
  });

  testWidgets('editing an existing rule pre-fills its fields', (tester) async {
    const existing = WeatherRule(
      id: 'zone2-humid', name: 'Zone 2 too humid', enabled: true, notify: true,
      zone: 'zone2', metric: 'air_humidity', op: '>', value: 70.0,
      durationMinutes: 1440, actuatorId: null, command: null,
    );

    await openDialog(tester, existing: existing);

    expect(find.widgetWithText(TextField, 'Zone 2 too humid'), findsOneWidget);
    expect(find.widgetWithText(TextField, '70.0'), findsOneWidget);
    expect(find.widgetWithText(TextField, '1440'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/widgets/rule_form_dialog_test.dart`
Expected: FAIL — `Error: Target of URI doesn't exist: 'package:greenhouse_app/screens/weather/rule_form_dialog.dart'`.

- [ ] **Step 3: Implement the dialog**

Create `app/lib/screens/weather/rule_form_dialog.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/weather_rule.dart';

const _weatherMetrics = <String, String>{
  'temperature': 'Temperature (°C)',
  'rain_mm_1h': 'Rain next hour (mm)',
  'humidity': 'Humidity (%)',
  'wind_kmh': 'Wind (km/h)',
  'uv_index': 'UV Index',
};

const _zoneMetrics = <String, String>{
  'soil_moisture': 'Soil moisture (%)',
  'air_humidity': 'Air humidity (%)',
  'air_temperature': 'Air temperature (°C)',
};

const _operators = ['>', '<', '>=', '<=', '=='];

/// Shows the add/edit rule form. Returns the resulting WeatherRule, or null
/// if cancelled. Pass `existing` to edit (fields pre-filled, same id kept);
/// omit it to create a new rule (a fresh id is generated).
Future<WeatherRule?> showRuleFormDialog(
  BuildContext context, {
  WeatherRule? existing,
  required List<String> zones,
  required List<String> actuatorIds,
}) {
  return showDialog<WeatherRule>(
    context: context,
    builder: (_) => _RuleFormDialog(existing: existing, zones: zones, actuatorIds: actuatorIds),
  );
}

class _RuleFormDialog extends StatefulWidget {
  final WeatherRule? existing;
  final List<String> zones;
  final List<String> actuatorIds;
  const _RuleFormDialog({required this.existing, required this.zones, required this.actuatorIds});

  @override
  State<_RuleFormDialog> createState() => _RuleFormDialogState();
}

class _RuleFormDialogState extends State<_RuleFormDialog> {
  late final TextEditingController _nameCtrl;
  late final TextEditingController _valueCtrl;
  late final TextEditingController _durationCtrl;
  String? _zone;
  late String _metric;
  late String _op;
  bool _hasAction = false;
  String? _actuatorId;
  String _command = 'ON';
  late bool _notify;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    _nameCtrl = TextEditingController(text: e?.name ?? '');
    _valueCtrl = TextEditingController(text: e?.value.toString() ?? '');
    _durationCtrl = TextEditingController(text: e?.durationMinutes?.toString() ?? '');
    _zone = e?.zone;
    _metric = e?.metric ?? _weatherMetrics.keys.first;
    _op = e?.op ?? '>';
    _hasAction = e?.actuatorId != null;
    _actuatorId = e?.actuatorId ?? (widget.actuatorIds.isNotEmpty ? widget.actuatorIds.first : null);
    _command = e?.command ?? 'ON';
    _notify = e?.notify ?? true;
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _valueCtrl.dispose();
    _durationCtrl.dispose();
    super.dispose();
  }

  Map<String, String> get _metricOptions => _zone == null ? _weatherMetrics : _zoneMetrics;

  void _onZoneChanged(String? zone) {
    setState(() {
      _zone = zone;
      // Reset metric to a valid option for the new zone/weather set.
      _metric = (zone == null ? _weatherMetrics : _zoneMetrics).keys.first;
    });
  }

  void _save() {
    final value = double.tryParse(_valueCtrl.text);
    final name = _nameCtrl.text.trim();
    if (value == null || name.isEmpty) return;

    int? duration;
    if (_durationCtrl.text.trim().isNotEmpty) {
      duration = int.tryParse(_durationCtrl.text.trim());
    }
    if (_zone != null && duration == null) return; // duration required for zone-specific rules

    final id = widget.existing?.id ??
        '${_zone ?? "weather"}-${_metric}-${DateTime.now().millisecondsSinceEpoch}';

    Navigator.pop(
      context,
      WeatherRule(
        id: id,
        name: name,
        enabled: widget.existing?.enabled ?? true,
        notify: _notify,
        zone: _zone,
        metric: _metric,
        op: _op,
        value: value,
        durationMinutes: duration,
        actuatorId: _hasAction ? _actuatorId : null,
        command: _hasAction ? _command : null,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.existing == null ? 'Add rule' : 'Edit rule'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _nameCtrl,
              decoration: const InputDecoration(labelText: 'Name', border: OutlineInputBorder()),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String?>(
              key: const Key('rule-form-zone-dropdown'),
              initialValue: _zone,
              decoration: const InputDecoration(labelText: 'Zone', border: OutlineInputBorder()),
              items: [
                const DropdownMenuItem<String?>(value: null, child: Text('Weather')),
                for (final z in widget.zones)
                  DropdownMenuItem<String?>(
                      value: z, child: Text('Zone ${z.replaceFirst('zone', '')}')),
              ],
              onChanged: _onZoneChanged,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: const Key('rule-form-metric-dropdown'),
              initialValue: _metricOptions.containsKey(_metric) ? _metric : _metricOptions.keys.first,
              decoration: const InputDecoration(labelText: 'Metric', border: OutlineInputBorder()),
              items: [
                for (final entry in _metricOptions.entries)
                  DropdownMenuItem(value: entry.key, child: Text(entry.value)),
              ],
              onChanged: (v) => setState(() => _metric = v!),
            ),
            const SizedBox(height: 12),
            Row(children: [
              Expanded(
                child: DropdownButtonFormField<String>(
                  initialValue: _op,
                  decoration: const InputDecoration(labelText: 'Operator', border: OutlineInputBorder()),
                  items: [for (final o in _operators) DropdownMenuItem(value: o, child: Text(o))],
                  onChanged: (v) => setState(() => _op = v!),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: _valueCtrl,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                  decoration:
                      const InputDecoration(labelText: 'Threshold value', border: OutlineInputBorder()),
                ),
              ),
            ]),
            const SizedBox(height: 12),
            TextField(
              controller: _durationCtrl,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                labelText: 'Duration (minutes)',
                helperText: _zone != null ? 'Required for zone-specific metrics' : 'Optional',
                border: const OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Also control a device'),
              value: _hasAction,
              onChanged: (v) => setState(() => _hasAction = v),
            ),
            if (_hasAction) ...[
              DropdownButtonFormField<String>(
                initialValue: _actuatorId,
                decoration: const InputDecoration(labelText: 'Actuator', border: OutlineInputBorder()),
                items: [
                  for (final a in widget.actuatorIds) DropdownMenuItem(value: a, child: Text(a)),
                ],
                onChanged: (v) => setState(() => _actuatorId = v),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: _command,
                decoration: const InputDecoration(labelText: 'Command', border: OutlineInputBorder()),
                items: const [
                  DropdownMenuItem(value: 'ON', child: Text('ON')),
                  DropdownMenuItem(value: 'OFF', child: Text('OFF')),
                ],
                onChanged: (v) => setState(() => _command = v!),
              ),
            ],
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Send a notification when this fires'),
              value: _notify,
              onChanged: (v) => setState(() => _notify = v),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(onPressed: _save, child: const Text('Save')),
      ],
    );
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/widgets/rule_form_dialog_test.dart`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/lib/screens/weather/rule_form_dialog.dart app/test/widgets/rule_form_dialog_test.dart
git commit -m "feat: add the rule builder dialog (zone/metric/duration/action/notify)"
```

---

### Task 8: Wire the rule builder into the Rules tab

**Files:**
- Modify: `app/lib/screens/weather/weather_screen.dart` (`_RulesList`, `_RuleCard`; add alert-settings section, add-rule button, delete)
- Test: `app/test/widgets/weather_screen_test.dart` (new file)

**Interfaces:**
- Consumes: `showRuleFormDialog` (Task 7), `notificationSettingsProvider`/`GreenhouseRepository.publishNotificationSettings` (Task 6), `WeatherRule` (Task 5).

- [ ] **Step 1: Write the failing widget tests**

Create `app/test/widgets/weather_screen_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/models/notification_settings.dart';
import 'package:greenhouse_app/models/weather_rule.dart';
import 'package:greenhouse_app/providers/actuators_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/screens/weather/weather_screen.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';

class MockConnection extends Mock implements GreenhouseConnection {}

void main() {
  setUpAll(() {
    registerFallbackValue(const ConnectionConfig(
      lanHost: '', remoteHost: '', port: 9001,
      tlsFingerprint: '', username: '', password: '',
      remoteUsername: '', remotePassword: '',
    ));
  });

  late MockConnection conn;

  setUp(() {
    conn = MockConnection();
    when(() => conn.events).thenAnswer((_) => const Stream.empty());
    when(() => conn.status).thenAnswer((_) => const Stream.empty());
    when(() => conn.disconnect()).thenAnswer((_) async {});
    when(() => conn.publishRaw(any(), any(), retain: any(named: 'retain')))
        .thenAnswer((_) async {});
  });

  const existingRule = WeatherRule(
    id: 'zone1-dry', name: 'Zone 1 soil dry', enabled: true, notify: true,
    zone: 'zone1', metric: 'soil_moisture', op: '<', value: 15.0,
    durationMinutes: 2880, actuatorId: null, command: null,
  );

  Widget buildApp() => ProviderScope(
        overrides: [
          repositoryProvider.overrideWith((ref) => GreenhouseRepository(connection: conn)),
          readingsProvider.overrideWith((ref) => Stream.value({
                'zone1': {'soil_moisture': 12.0},
              })),
          actuatorsProvider.overrideWith((ref) => Stream.value(<String, ActuatorState>{})),
          notificationSettingsProvider.overrideWith(
              (ref) => Stream.value(const NotificationSettings(frostForecast: true, dailySummary: true))),
        ],
        child: const MaterialApp(home: WeatherScreen()),
      );

  testWidgets('toggling a rule notify switch publishes updated rules', (tester) async {
    // rulesProvider isn't directly overridable (it's derived from the
    // repository's live stream) — seed it by having the mocked connection
    // emit a RulesPayloadRaw-equivalent through the repository. Simpler:
    // override repositoryProvider with a repo pre-loaded via publishRules.
    final repo = GreenhouseRepository(connection: conn);
    await repo.publishRules([existingRule]);

    await tester.pumpWidget(ProviderScope(
      overrides: [
        repositoryProvider.overrideWith((ref) => repo),
        readingsProvider.overrideWith((ref) => Stream.value({
              'zone1': {'soil_moisture': 12.0},
            })),
        actuatorsProvider.overrideWith((ref) => Stream.value(<String, ActuatorState>{})),
        notificationSettingsProvider.overrideWith((ref) =>
            Stream.value(const NotificationSettings(frostForecast: true, dailySummary: true))),
      ],
      child: const MaterialApp(home: WeatherScreen()),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Rules'));
    await tester.pumpAndSettle();

    expect(find.text('Zone 1 soil dry'), findsOneWidget);

    // The notify switch is the last Switch on the rule card (enabled switch
    // comes first in trailing order — see implementation step below).
    final notifySwitch = find.byKey(const Key('rule-notify-switch-zone1-dry'));
    expect(notifySwitch, findsOneWidget);
    await tester.tap(notifySwitch);
    await tester.pumpAndSettle();

    verify(() => conn.publishRaw('greenhouse/rules/update', any(), retain: true)).called(greaterThan(0));
  });

  testWidgets('alert settings toggles publish notification settings', (tester) async {
    await tester.pumpWidget(buildApp());
    await tester.pumpAndSettle();
    await tester.tap(find.text('Rules'));
    await tester.pumpAndSettle();

    final frostSwitch = find.byKey(const Key('alert-settings-frost-switch'));
    expect(frostSwitch, findsOneWidget);
    await tester.tap(frostSwitch);
    await tester.pumpAndSettle();

    verify(() => conn.publishRaw(
          'greenhouse/settings/notifications',
          any(),
          retain: true,
        )).called(1);
  });
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/widgets/weather_screen_test.dart`
Expected: FAIL (compile errors — `_RuleCard`'s switch has no key yet, no alert-settings section exists, `weather_screen.dart` itself still references the old `WeatherRule` API from before Task 5's model rewrite).

- [ ] **Step 3: Rewrite `_RulesTab`/`_RulesList`/`_RuleCard` and add the alert-settings section**

In `app/lib/screens/weather/weather_screen.dart`, add the imports:

```dart
import 'package:greenhouse_app/models/notification_settings.dart';
import 'package:greenhouse_app/providers/actuators_provider.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/screens/weather/rule_form_dialog.dart';
```

Replace the entire `_RulesTab` class with:

```dart
class _RulesTab extends ConsumerWidget {
  const _RulesTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final rulesAsync = ref.watch(rulesProvider);
    return rulesAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.rule_folder_outlined, size: 48, color: Colors.grey[400]),
              const SizedBox(height: 12),
              const Text('No rules received yet.\nPublish a rules-get request or wait for the Pi.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.grey)),
            ],
          ),
        ),
      ),
      data: (rules) => _RulesList(rules: rules),
    );
  }
}

class _AlertSettingsCard extends ConsumerWidget {
  const _AlertSettingsCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settingsAsync = ref.watch(notificationSettingsProvider);
    final settings = settingsAsync.value ?? const NotificationSettings(frostForecast: true, dailySummary: true);

    void publish(NotificationSettings updated) =>
        ref.read(repositoryProvider).publishNotificationSettings(updated);

    return Card(
      margin: const EdgeInsets.fromLTRB(8, 4, 8, 8),
      child: Column(
        children: [
          SwitchListTile(
            key: const Key('alert-settings-frost-switch'),
            title: const Text('Frost forecast alerts'),
            value: settings.frostForecast,
            onChanged: (v) => publish(settings.copyWith(frostForecast: v)),
          ),
          SwitchListTile(
            key: const Key('alert-settings-daily-switch'),
            title: const Text('Daily weather summary'),
            value: settings.dailySummary,
            onChanged: (v) => publish(settings.copyWith(dailySummary: v)),
          ),
        ],
      ),
    );
  }
}
```

Replace `_RulesListState`'s `_toggle`/`_editThreshold` methods and `build()` with:

```dart
  void _toggle(int index, bool enabled) {
    final updated = List<WeatherRule>.from(_rules);
    updated[index] = updated[index].copyWith(enabled: enabled);
    setState(() => _rules = updated);
    ref.read(repositoryProvider).publishRules(updated);
  }

  void _toggleNotify(int index, bool notify) {
    final updated = List<WeatherRule>.from(_rules);
    updated[index] = updated[index].copyWith(notify: notify);
    setState(() => _rules = updated);
    ref.read(repositoryProvider).publishRules(updated);
  }

  void _delete(int index) {
    final updated = List<WeatherRule>.from(_rules)..removeAt(index);
    setState(() => _rules = updated);
    ref.read(repositoryProvider).publishRules(updated);
  }

  List<String> _knownZones() {
    final readings = ref.read(readingsProvider).value ?? {};
    return readings.keys.where((z) => z != 'weather').toList()..sort();
  }

  List<String> _knownActuators() {
    final actuators = ref.read(actuatorsProvider).value ?? {};
    return actuators.keys.toList()..sort();
  }

  void _addRule() async {
    final result = await showRuleFormDialog(
      context,
      zones: _knownZones(),
      actuatorIds: _knownActuators(),
    );
    if (result != null) {
      final updated = List<WeatherRule>.from(_rules)..add(result);
      setState(() => _rules = updated);
      ref.read(repositoryProvider).publishRules(updated);
    }
  }

  void _editRule(int index) async {
    final result = await showRuleFormDialog(
      context,
      existing: _rules[index],
      zones: _knownZones(),
      actuatorIds: _knownActuators(),
    );
    if (result != null) {
      final updated = List<WeatherRule>.from(_rules);
      updated[index] = result;
      setState(() => _rules = updated);
      ref.read(repositoryProvider).publishRules(updated);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        const _AlertSettingsCard(),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
          child: Row(children: [
            const Icon(Icons.info_outline, size: 16, color: Colors.grey),
            const SizedBox(width: 6),
            Expanded(
              child: Text('Changes sync to the Pi immediately.',
                  style: TextStyle(fontSize: 12, color: Colors.grey[600])),
            ),
            TextButton.icon(
              icon: const Icon(Icons.add, size: 16),
              label: const Text('Add rule'),
              onPressed: _addRule,
            ),
          ]),
        ),
        Expanded(
          child: ListView.builder(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            itemCount: _rules.length,
            itemBuilder: (context, i) => _RuleCard(
              rule: _rules[i],
              onToggle: (v) => _toggle(i, v),
              onToggleNotify: (v) => _toggleNotify(i, v),
              onEdit: () => _editRule(i),
              onDelete: () => _delete(i),
            ),
          ),
        ),
      ],
    );
  }
```

Replace the whole `_RuleCard` class with:

```dart
class _RuleCard extends StatelessWidget {
  final WeatherRule rule;
  final ValueChanged<bool> onToggle;
  final ValueChanged<bool> onToggleNotify;
  final VoidCallback onEdit;
  final VoidCallback onDelete;
  const _RuleCard({
    required this.rule,
    required this.onToggle,
    required this.onToggleNotify,
    required this.onEdit,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        leading: CircleAvatar(
          backgroundColor: rule.enabled
              ? AppColors.brand.withAlpha(30)
              : Colors.grey.withAlpha(30),
          child: Icon(
            _ruleIcon(rule.metric),
            color: rule.enabled ? AppColors.brand : Colors.grey,
            size: 20,
          ),
        ),
        title: Text(rule.name,
            style: TextStyle(
              fontWeight: FontWeight.w600,
              color: rule.enabled ? null : Colors.grey,
            )),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('${rule.zoneLabel}: ${rule.metricLabel} ${rule.op} ${rule.value}'
                  '${rule.durationMinutes != null ? " for ${rule.durationMinutes} min" : ""}',
                  style: const TextStyle(fontSize: 12)),
              Text(
                rule.actuatorId != null
                    ? '→ ${rule.actuatorId}: ${rule.command}'
                    : '→ alert only',
                style: TextStyle(fontSize: 12, color: Colors.grey[600]),
              ),
            ],
          ),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            IconButton(
              icon: const Icon(Icons.edit_outlined, size: 18),
              tooltip: 'Edit rule',
              onPressed: onEdit,
            ),
            IconButton(
              icon: const Icon(Icons.delete_outline, size: 18),
              tooltip: 'Delete rule',
              onPressed: onDelete,
            ),
            IconButton(
              key: Key('rule-notify-switch-${rule.id}'),
              icon: Icon(rule.notify ? Icons.notifications_active : Icons.notifications_off,
                  size: 18, color: rule.notify ? AppColors.brand : Colors.grey),
              tooltip: rule.notify ? 'Notifications on' : 'Notifications off',
              onPressed: () => onToggleNotify(!rule.notify),
            ),
            Switch(
              value: rule.enabled,
              onChanged: onToggle,
              activeColor: AppColors.brand,
            ),
          ],
        ),
        isThreeLine: true,
      ),
    );
  }

  IconData _ruleIcon(String metric) {
    switch (metric) {
      case 'temperature':
      case 'air_temperature': return Icons.thermostat;
      case 'rain_mm_1h':      return Icons.umbrella;
      case 'humidity':
      case 'air_humidity':    return Icons.water_drop;
      case 'soil_moisture':   return Icons.grass;
      case 'wind_kmh':        return Icons.air;
      case 'uv_index':        return Icons.wb_sunny;
      default:                return Icons.rule;
    }
  }
}
```

(Note: the plan's test above expects `rule-notify-switch-<id>` to be tappable via `find.byKey` and toggle notify — implemented here as an `IconButton` rather than a `Switch` to fit inline with the existing enabled `Switch` in `trailing` without crowding; either interactive widget satisfies the test, which only checks the key exists and taps it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/widgets/weather_screen_test.dart`
Expected: `2 passed`.

- [ ] **Step 5: Run the full app test suite and analyzer**

Run: `cd app && flutter analyze`
Expected: `No issues found!`

Run: `cd app && flutter test`
Expected: all tests pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/lib/screens/weather/weather_screen.dart app/test/widgets/weather_screen_test.dart
git commit -m "feat: wire the rule builder into the Rules tab, add alert settings UI"
```

**Manual bench-test step (cannot be verified from this dev sandbox):** once deployed to a real Pi (`deploy.ps1`, which reruns `install.sh` and seeds the six new rules — only on units where `rules.json` doesn't already exist; an already-provisioned Pi like the one used for the FCM bench test needs the new rules added manually via SSH or through the app's new "Add rule" button), verify: creating a new zone-specific rule from the app actually appears in the Pi's `rules.json` within one poll cycle; toggling a rule's notify switch off means it no longer pushes (but its MQTT alert and actuator action still fire); toggling off "Frost forecast alerts" suppresses that push specifically while daily summary and rule-based alerts keep working.
