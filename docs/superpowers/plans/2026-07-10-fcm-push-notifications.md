# FCM Push Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Weather alerts (and, later, camera motion alerts) reach the phone as a real Android OS notification even when the app is fully closed, by adding Firebase Cloud Messaging on top of the Pi's existing MQTT-driven alert system.

**Architecture:** The Pi keeps publishing `greenhouse/weather/alert` over MQTT exactly as today (used by the Weather screen's in-app banner). A new `pi/shared/push.py` helper additionally calls Firebase Cloud Messaging, resolving the current set of registered devices by reading retained MQTT messages the app publishes to `greenhouse/app/fcm_token/<device-uuid>`. The app registers/refreshes its token through the existing `GreenhouseConnection.publishRaw(..., retain: true)` path, and receives FCM messages via `firebase_messaging` — in foreground via an `onMessage` handler routed into the existing `NotificationService`, and in background/terminated automatically by Android with no app code involved.

**Tech Stack:** Python (`firebase-admin`, `mosquitto_sub`/`mosquitto_pub` CLI — matching the Pi's existing no-persistent-MQTT-client convention), Flutter (`firebase_core`, `firebase_messaging`), pytest, `flutter test` + `mocktail`.

## Global Constraints

- Android-only — no iOS work (iOS is already untested for this app).
- No token expiry/cleanup logic — a stale/invalid token fails silently per-device, logged, never blocks other devices.
- Multi-device: the Pi supports more than one registered phone, via one retained MQTT topic per device (`greenhouse/app/fcm_token/<device-uuid>`), not a single overwritten value.
- The existing MQTT publish (`greenhouse/weather/alert`) and the Weather screen's in-app banner (`weatherAlertsProvider`) are unchanged — FCM is an added delivery channel, not a replacement for that live state.
- A push failure (missing Firebase setup, bad token, network error) must never raise out of `send_push()` or block `weather.py`'s rule-evaluation loop.
- Camera motion-alert integration is explicitly out of scope — this plan only builds `send_push()` as a reusable helper.
- Firebase service-account credentials live at `/etc/greenhouse/firebase-service-account.json` on the Pi, owned `pi:pi` mode 600 (not root-only — `greenhouse-weather.service` runs as `User=pi`) — obtaining this file is a manual Firebase Console step, outside this plan's code changes.

---

### Task 1: Pi shared push helper (`pi/shared/push.py`)

**Files:**
- Create: `pi/shared/push.py`
- Test: `pi/tests/test_push.py`

**Interfaces:**
- Produces: `push.parse_fcm_tokens(sub_output: str) -> dict[str, str]`, `push.get_registered_tokens() -> dict[str, str]`, `push.send_push(title: str, body: str) -> None`, module attribute `push._FIREBASE_AVAILABLE: bool`, module attribute `push.messaging` (the `firebase_admin.messaging` submodule, present only when `_FIREBASE_AVAILABLE`), and `push._ensure_firebase_app` (a zero-arg function later tasks/tests may monkeypatch).

- [ ] **Step 1: Install `firebase-admin` in this dev environment (needed to write/run this task's tests)**

Run: `py -3 -m pip install firebase-admin`
Expected: package installs successfully (or reports "Requirement already satisfied").

- [ ] **Step 2: Write the failing tests**

Create `pi/tests/test_push.py`:

```python
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import push


def test_parse_fcm_tokens_single_device():
    output = 'greenhouse/app/fcm_token/device-a token-abc123\n'
    assert push.parse_fcm_tokens(output) == {'device-a': 'token-abc123'}


def test_parse_fcm_tokens_multiple_devices():
    output = (
        'greenhouse/app/fcm_token/device-a token-abc\n'
        'greenhouse/app/fcm_token/device-b token-xyz\n'
    )
    assert push.parse_fcm_tokens(output) == {
        'device-a': 'token-abc',
        'device-b': 'token-xyz',
    }


def test_parse_fcm_tokens_ignores_blank_lines():
    output = '\ngreenhouse/app/fcm_token/device-a token-abc\n\n'
    assert push.parse_fcm_tokens(output) == {'device-a': 'token-abc'}


def test_parse_fcm_tokens_empty_output_returns_empty_dict():
    assert push.parse_fcm_tokens('') == {}


def test_get_registered_tokens_parses_mosquitto_sub_output(monkeypatch):
    fake_result = MagicMock()
    fake_result.stdout = 'greenhouse/app/fcm_token/device-a token-abc\n'
    monkeypatch.setattr(
        push.subprocess, 'run', lambda *a, **k: fake_result)

    assert push.get_registered_tokens() == {'device-a': 'token-abc'}


def test_get_registered_tokens_returns_empty_dict_on_subprocess_error(monkeypatch):
    def _raise(*a, **k):
        raise OSError('mosquitto_sub not found')
    monkeypatch.setattr(push.subprocess, 'run', _raise)

    assert push.get_registered_tokens() == {}


def test_send_push_calls_messaging_send_once_per_token(monkeypatch):
    monkeypatch.setattr(push, '_FIREBASE_AVAILABLE', True)
    monkeypatch.setattr(push, '_ensure_firebase_app', lambda: None)
    monkeypatch.setattr(push, 'get_registered_tokens',
                         lambda: {'device-a': 'token-a', 'device-b': 'token-b'})
    fake_messaging = MagicMock()
    monkeypatch.setattr(push, 'messaging', fake_messaging)

    push.send_push('Frost warning', 'Frost expected tonight')

    assert fake_messaging.send.call_count == 2


def test_send_push_continues_after_one_token_fails(monkeypatch):
    monkeypatch.setattr(push, '_FIREBASE_AVAILABLE', True)
    monkeypatch.setattr(push, '_ensure_firebase_app', lambda: None)
    monkeypatch.setattr(push, 'get_registered_tokens',
                         lambda: {'device-a': 'bad-token', 'device-b': 'good-token'})

    fake_messaging = MagicMock()
    fake_messaging.Message.side_effect = (
        lambda notification=None, token=None: MagicMock(token=token))

    def _send(message):
        if message.token == 'bad-token':
            raise Exception('registration-token-not-registered')
        return 'projects/x/messages/ok'

    fake_messaging.send.side_effect = _send
    monkeypatch.setattr(push, 'messaging', fake_messaging)

    push.send_push('Test', 'Body')  # must not raise

    assert fake_messaging.send.call_count == 2


def test_send_push_noop_when_no_tokens_registered(monkeypatch):
    monkeypatch.setattr(push, '_FIREBASE_AVAILABLE', True)
    monkeypatch.setattr(push, 'get_registered_tokens', lambda: {})
    called = MagicMock()
    monkeypatch.setattr(push, '_ensure_firebase_app', called)

    push.send_push('Test', 'Body')

    called.assert_not_called()


def test_send_push_noop_when_firebase_not_available(monkeypatch, capsys):
    monkeypatch.setattr(push, '_FIREBASE_AVAILABLE', False)

    push.send_push('Test', 'Body')

    captured = capsys.readouterr()
    assert 'firebase_admin not installed' in captured.out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd pi && py -3 -m pytest tests/test_push.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'push'` (the file doesn't exist yet).

- [ ] **Step 4: Write the implementation**

Create `pi/shared/push.py`:

```python
#!/usr/bin/env python3
"""Shared FCM push-notification helper for greenhouse Pi services.

Reads currently-registered device tokens from retained MQTT messages
(published by the app to greenhouse/app/fcm_token/<device-uuid>, one retained
topic per device) and sends a push via Firebase Cloud Messaging to each one.
Never raises — a missing Firebase setup or a bad/expired token for one
device must not stop alerts from reaching the rest, or block whatever
rule-evaluation loop called send_push().
"""
import subprocess

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    _FIREBASE_AVAILABLE = True
except ImportError:
    _FIREBASE_AVAILABLE = False

MQTT_HOST = '127.0.0.1'
MQTT_PORT = '1883'
FCM_TOKEN_TOPIC_FILTER = 'greenhouse/app/fcm_token/+'
FIREBASE_CREDENTIALS = '/etc/greenhouse/firebase-service-account.json'

_firebase_app = None


def parse_fcm_tokens(sub_output: str) -> dict[str, str]:
    """Parse `mosquitto_sub -v` output for the fcm_token wildcard into
    {device_uuid: token}."""
    tokens: dict[str, str] = {}
    for line in sub_output.splitlines():
        line = line.strip()
        if not line:
            continue
        topic, _, payload = line.partition(' ')
        if not payload:
            continue
        device_id = topic.rsplit('/', 1)[-1]
        tokens[device_id] = payload
    return tokens


def get_registered_tokens() -> dict[str, str]:
    """Query the broker for every currently-retained fcm_token/<device> value."""
    try:
        result = subprocess.run(
            ['mosquitto_sub', '-h', MQTT_HOST, '-p', MQTT_PORT,
             '-t', FCM_TOKEN_TOPIC_FILTER, '-v', '-W', '3'],
            capture_output=True, text=True, timeout=6,
        )
        return parse_fcm_tokens(result.stdout)
    except Exception as e:
        print(f'[push] WARN: could not read registered tokens: {e}', flush=True)
        return {}


def _ensure_firebase_app():
    global _firebase_app
    if _firebase_app is None:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS)
        _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


def send_push(title: str, body: str) -> None:
    if not _FIREBASE_AVAILABLE:
        print('[push] WARN: firebase_admin not installed, skipping push', flush=True)
        return
    tokens = get_registered_tokens()
    if not tokens:
        return
    try:
        _ensure_firebase_app()
    except Exception as e:
        print(f'[push] WARN: Firebase init failed, skipping push: {e}', flush=True)
        return
    for device_id, token in tokens.items():
        try:
            messaging.send(messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                token=token,
            ))
        except Exception as e:
            print(f'[push] WARN: send failed for device {device_id}: {e}', flush=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd pi && py -3 -m pytest tests/test_push.py -v`
Expected: `10 passed`

- [ ] **Step 6: Commit**

```bash
git add pi/shared/push.py pi/tests/test_push.py
git commit -m "feat: add FCM push helper (pi/shared/push.py)"
```

---

### Task 2: Wire weather.py's alert sites to send push

**Files:**
- Modify: `pi/scripts/weather.py:1-16` (imports), `weather.py`'s `_fire()` inside `eval_rules()` (~line 172-185), `maybe_send_daily_summary()` (~line 256-286), `maybe_send_frost_alert()` (~line 291-310)
- Test: `pi/tests/test_weather_rules.py`

**Interfaces:**
- Consumes: `push.send_push(title: str, body: str) -> None` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `pi/tests/test_weather_rules.py` (add `from datetime import datetime` to the existing imports at the top, alongside the existing `import weather` / `import recorder`):

```python
def test_eval_rules_live_metric_sends_push_on_fire(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    rule = {
        'id': 'r1', 'name': 'High Temp', 'enabled': True,
        'trigger': {'metric': 'temperature', 'op': '>', 'value': 30.0},
        'action': {'actuator': 'fan', 'command': 'on'},
    }
    weather.eval_rules([rule], {'temperature': 35.0})

    assert len(pushed) == 1
    title, body = pushed[0]
    assert title == 'High Temp'
    assert 'High Temp' in body


def test_eval_rules_does_not_push_when_rule_does_not_fire(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    rule = {
        'id': 'r1', 'name': 'High Temp', 'enabled': True,
        'trigger': {'metric': 'temperature', 'op': '>', 'value': 30.0},
        'action': {'actuator': 'fan', 'command': 'on'},
    }
    weather.eval_rules([rule], {'temperature': 20.0})

    assert pushed == []


def test_maybe_send_daily_summary_sends_push(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    monkeypatch.setattr(weather, '_last_summary_date', None)

    class _FrozenClock:
        @staticmethod
        def now():
            return datetime(2026, 7, 10, 7, 0, 0)
    monkeypatch.setattr(weather, 'datetime', _FrozenClock)

    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    data = {'hourly': {
        'temperature_2m': [20.0] * 24,
        'precipitation': [0.0] * 24,
    }}
    weather.maybe_send_daily_summary(data, {'temperature': 22.0, 'wind_kmh': 5.0})

    assert len(pushed) == 1
    assert pushed[0][0] == "Today's forecast"


def test_maybe_send_frost_alert_sends_push(monkeypatch):
    monkeypatch.setattr(weather, 'mqtt_publish', lambda *a, **k: None)
    monkeypatch.setattr(weather, '_last_frost_alert', None)
    pushed = []
    monkeypatch.setattr(weather, 'send_push', lambda title, body: pushed.append((title, body)))

    data = {'hourly': {'temperature_2m': [-2.0] * 12}}
    weather.maybe_send_frost_alert(data)

    assert len(pushed) == 1
    assert pushed[0][0] == 'Frost warning'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && py -3 -m pytest tests/test_weather_rules.py -v`
Expected: FAIL — `AttributeError: module 'weather' has no attribute 'send_push'` (`monkeypatch.setattr` fails on a nonexistent attribute).

- [ ] **Step 3: Wire the implementation**

In `pi/scripts/weather.py`, after the existing imports (after the `from datetime import datetime, timezone` line), add:

```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
from push import send_push
```

In `_fire()` (inside `eval_rules()`), immediately after the existing `mqtt_publish('greenhouse/weather/alert', json.dumps(alert))` line:

```python
        mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
        send_push(rule.get('name', 'Rule'), message)
```

In `maybe_send_daily_summary()`, immediately after its existing `mqtt_publish('greenhouse/weather/alert', json.dumps(alert))` line:

```python
        mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
        send_push("Today's forecast", summary_msg)
        _last_summary_date = today
```

(Note: this keeps the existing `_last_summary_date = today` line right after — just insert the new `send_push` call between the `mqtt_publish` call and it.)

In `maybe_send_frost_alert()`, immediately after its existing `mqtt_publish('greenhouse/weather/alert', json.dumps(alert))` line:

```python
            mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
            send_push('Frost warning', alert['message'])
            _last_frost_alert = today
```

(Same note: keep the existing `_last_frost_alert = today` line right after.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && py -3 -m pytest tests/test_weather_rules.py -v`
Expected: `12 passed` (8 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add pi/scripts/weather.py pi/tests/test_weather_rules.py
git commit -m "feat: send FCM push alongside every weather.py MQTT alert"
```

---

### Task 3: Pi dependency install (`install.sh`)

**Files:**
- Modify: `pi/install.sh:21-28` (apt package list)

**Interfaces:**
- None (shell script; no interface consumed/produced by other tasks).

- [ ] **Step 1: Check current syntax baseline**

Run: `bash -n pi/install.sh`
Expected: no output (valid syntax).

- [ ] **Step 2: Add `python3-pip` to the apt package list**

In `pi/install.sh`, change:

```bash
apt-get install -y -qq \
  mosquitto mosquitto-clients \
  python3-flask \
  python3-paho-mqtt \
  openssl \
  dnsmasq-base \
  iptables \
  rfkill \
  avahi-daemon
```

to:

```bash
apt-get install -y -qq \
  mosquitto mosquitto-clients \
  python3-flask \
  python3-paho-mqtt \
  python3-pip \
  openssl \
  dnsmasq-base \
  iptables \
  rfkill \
  avahi-daemon
```

- [ ] **Step 3: Add the `firebase-admin` pip install step**

Immediately after the `apt-get install` block (before the `echo "==> Creating directories..."` line), add:

```bash
echo "==> Installing firebase-admin (for push notifications)..."
# Not available as an apt package. Trixie's Python is "externally managed"
# (PEP 668) — --break-system-packages is required for a direct system-wide
# pip install here, matching this project's existing no-venv convention.
# Compatibility on the Pi Zero W's ARMv6 image is unverified until bench-tested
# (see docs/superpowers/specs/2026-07-10-fcm-push-notifications-design.md).
pip3 install --break-system-packages firebase-admin
```

- [ ] **Step 4: Add a reminder for the manual service-account key step**

Find the `echo "==> Creating directories..."` line and its following `mkdir -p /etc/greenhouse ...` line; immediately after that `mkdir -p` line, add:

```bash
if [ ! -f /etc/greenhouse/firebase-service-account.json ]; then
  echo "NOTE: /etc/greenhouse/firebase-service-account.json not found."
  echo "      Push notifications will be skipped until you copy your Firebase"
  echo "      service-account key there (see the FCM push notifications spec)."
fi
```

- [ ] **Step 5: Verify syntax**

Run: `bash -n pi/install.sh`
Expected: no output (valid syntax).

- [ ] **Step 6: Verify the new lines are present**

Run: `grep -n "firebase-admin\|python3-pip\|firebase-service-account" pi/install.sh`
Expected: 5 matching lines (the apt package, the "Installing firebase-admin" echo, the pip install command, and the two service-account-check lines).

- [ ] **Step 7: Commit**

```bash
git add pi/install.sh
git commit -m "feat: install firebase-admin for push notifications"
```

---

### Task 4: App FCM token registration service

**Files:**
- Modify: `app/pubspec.yaml` (add `firebase_core`, `firebase_messaging`)
- Modify: `app/android/settings.gradle.kts` (add Google Services plugin)
- Modify: `app/android/app/build.gradle.kts` (apply Google Services plugin)
- Modify: `app/android/app/src/main/AndroidManifest.xml` (default notification channel meta-data)
- Modify: `app/lib/repository/greenhouse_repository.dart` (add `registerFcmToken`)
- Create: `app/lib/services/fcm_token_service.dart`
- Test: `app/test/services/fcm_token_service_test.dart`

**Interfaces:**
- Consumes: `GreenhouseRepository.registerFcmToken(String deviceId, String token) -> Future<void>` (added in this task), `GreenhouseConnection.publishRaw(String topic, String payload, {bool retain}) -> Future<void>` (existing).
- Produces: `FcmTokenService(GreenhouseRepository repository, {readSecure, writeSecure, getToken, onTokenRefresh, onMessage})` with methods `Future<void> registerToken()`, `void listenForRefresh()`, and `void listenForForegroundMessages(void Function(String title, String body) onNotification)` — all used by Task 5's `app.dart` wiring.

- [ ] **Step 1: Add Firebase packages to pubspec.yaml**

In `app/pubspec.yaml`, add to the `dependencies:` block (after `fl_chart: ^1.1.0`):

```yaml
  firebase_core: ^3.8.0
  firebase_messaging: ^15.1.5
```

Run: `cd app && flutter pub get`
Expected: `Got dependencies!` (or a version resolution message — if a conflict is reported, adjust these two version numbers to the nearest versions `flutter pub get` resolves cleanly; this is an environment-specific pub.dev resolution detail, not a design change).

- [ ] **Step 2: Wire the Android Gradle plugin**

In `app/android/settings.gradle.kts`, change:

```kotlin
plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.1.0" apply false
}
```

to:

```kotlin
plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.1.0" apply false
    id("com.google.gms.google-services") version "4.4.2" apply false
}
```

In `app/android/app/build.gradle.kts`, change:

```kotlin
plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}
```

to:

```kotlin
plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
    id("com.google.gms.google-services")
}
```

**Manual step (cannot be done from code):** create a Firebase project at
console.firebase.google.com, add an Android app with package name
`com.greenhouse.greenhouse_app`, download `google-services.json`, and place
it at `app/android/app/google-services.json`. Without this file, `flutter
build apk` / `flutter run` will fail after this step — `flutter test` (Dart
unit tests, used to verify this task) does not require it.

- [ ] **Step 3: Add the default notification channel meta-data**

In `app/android/app/src/main/AndroidManifest.xml`, change:

```xml
        <meta-data
            android:name="flutterEmbedding"
            android:value="2" />
    </application>
```

to:

```xml
        <meta-data
            android:name="flutterEmbedding"
            android:value="2" />
        <meta-data
            android:name="com.google.firebase.messaging.default_notification_channel_id"
            android:value="greenhouse_weather" />
    </application>
```

- [ ] **Step 4: Add `registerFcmToken` to the repository**

In `app/lib/repository/greenhouse_repository.dart`, immediately after the existing `publishLocation` method (~line 132), add:

```dart

  /// Registers this device's current FCM token with the Pi, retained per
  /// device so weather.py (and later camera motion alerts) can look up
  /// every currently-registered device on demand.
  Future<void> registerFcmToken(String deviceId, String token) async {
    await connection.publishRaw(
        'greenhouse/app/fcm_token/$deviceId', token, retain: true);
  }
```

- [ ] **Step 5: Write the failing test for FcmTokenService**

Create `app/test/services/fcm_token_service_test.dart`:

```dart
import 'dart:async';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';
import 'package:greenhouse_app/services/fcm_token_service.dart';

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
  late GreenhouseRepository repo;

  setUp(() {
    conn = MockConnection();
    when(() => conn.events).thenAnswer((_) => const Stream.empty());
    when(() => conn.status).thenAnswer((_) => const Stream.empty());
    when(() => conn.publishRaw(any(), any(), retain: any(named: 'retain')))
        .thenAnswer((_) async {});
    repo = GreenhouseRepository(connection: conn);
  });

  tearDown(() => repo.disconnect());

  test('registerToken generates and persists a device id, then publishes retained', () async {
    String? storedId;
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => storedId,
      writeSecure: (key, value) async => storedId = value,
      getToken: () async => 'token-123',
      onTokenRefresh: () => const Stream.empty(),
    );

    await service.registerToken();

    expect(storedId, isNotNull);
    verify(() => conn.publishRaw(
          'greenhouse/app/fcm_token/$storedId',
          'token-123',
          retain: true,
        )).called(1);
  });

  test('registerToken reuses an existing stored device id', () async {
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'existing-device-id',
      writeSecure: (key, value) async => fail('should not write a new id'),
      getToken: () async => 'token-456',
      onTokenRefresh: () => const Stream.empty(),
    );

    await service.registerToken();

    verify(() => conn.publishRaw(
          'greenhouse/app/fcm_token/existing-device-id',
          'token-456',
          retain: true,
        )).called(1);
  });

  test('registerToken does nothing when no token is available yet', () async {
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'existing-device-id',
      writeSecure: (key, value) async {},
      getToken: () async => null,
      onTokenRefresh: () => const Stream.empty(),
    );

    await service.registerToken();

    verifyNever(() => conn.publishRaw(any(), any(), retain: any(named: 'retain')));
  });

  test('listenForRefresh republishes when the token changes', () async {
    final refreshCtrl = StreamController<String>();
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'device-xyz',
      writeSecure: (key, value) async {},
      getToken: () async => 'unused',
      onTokenRefresh: () => refreshCtrl.stream,
    );

    service.listenForRefresh();
    refreshCtrl.add('refreshed-token');
    await Future(() {});
    await Future(() {});

    verify(() => conn.publishRaw(
          'greenhouse/app/fcm_token/device-xyz',
          'refreshed-token',
          retain: true,
        )).called(1);
    await refreshCtrl.close();
  });

  test('listenForForegroundMessages invokes the callback with title/body', () async {
    final messageCtrl = StreamController<RemoteMessage>();
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'device-xyz',
      writeSecure: (key, value) async {},
      getToken: () async => 'unused',
      onTokenRefresh: () => const Stream.empty(),
      onMessage: () => messageCtrl.stream,
    );

    String? gotTitle;
    String? gotBody;
    service.listenForForegroundMessages((title, body) {
      gotTitle = title;
      gotBody = body;
    });

    messageCtrl.add(const RemoteMessage(
      notification: RemoteNotification(title: 'Frost warning', body: 'Frost expected tonight'),
    ));
    await Future(() {});

    expect(gotTitle, 'Frost warning');
    expect(gotBody, 'Frost expected tonight');
    await messageCtrl.close();
  });

  test('listenForForegroundMessages falls back to defaults when notification fields are missing', () async {
    final messageCtrl = StreamController<RemoteMessage>();
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'device-xyz',
      writeSecure: (key, value) async {},
      getToken: () async => 'unused',
      onTokenRefresh: () => const Stream.empty(),
      onMessage: () => messageCtrl.stream,
    );

    String? gotTitle;
    String? gotBody;
    service.listenForForegroundMessages((title, body) {
      gotTitle = title;
      gotBody = body;
    });

    messageCtrl.add(const RemoteMessage());
    await Future(() {});

    expect(gotTitle, 'Greenhouse');
    expect(gotBody, '');
    await messageCtrl.close();
  });
}
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd app && flutter test test/services/fcm_token_service_test.dart`
Expected: FAIL — `Error: Error when reading 'lib/services/fcm_token_service.dart': No such file or directory`

- [ ] **Step 7: Write the implementation**

Create `app/lib/services/fcm_token_service.dart`:

```dart
import 'dart:math';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';

const _deviceIdKey = 'greenhouse_device_id';

/// Registers this install's FCM token with the Pi (retained, per-device
/// topic) so weather.py — and later, camera motion alerts — can push to it
/// even when the app is fully closed.
class FcmTokenService {
  final GreenhouseRepository _repository;
  final Future<String?> Function(String key) _readSecure;
  final Future<void> Function(String key, String value) _writeSecure;
  final Future<String?> Function() _getToken;
  final Stream<String> Function() _onTokenRefresh;
  final Stream<RemoteMessage> Function() _onMessage;

  FcmTokenService(
    this._repository, {
    Future<String?> Function(String key)? readSecure,
    Future<void> Function(String key, String value)? writeSecure,
    Future<String?> Function()? getToken,
    Stream<String> Function()? onTokenRefresh,
    Stream<RemoteMessage> Function()? onMessage,
  })  : _readSecure = readSecure ??
            ((key) => const FlutterSecureStorage().read(key: key)),
        _writeSecure = writeSecure ??
            ((key, value) =>
                const FlutterSecureStorage().write(key: key, value: value)),
        _getToken = getToken ?? FirebaseMessaging.instance.getToken,
        _onTokenRefresh =
            onTokenRefresh ?? (() => FirebaseMessaging.instance.onTokenRefresh),
        _onMessage = onMessage ?? (() => FirebaseMessaging.onMessage);

  Future<String> _deviceId() async {
    final existing = await _readSecure(_deviceIdKey);
    if (existing != null) return existing;
    final id = _generateId();
    await _writeSecure(_deviceIdKey, id);
    return id;
  }

  String _generateId() {
    final rand = Random.secure();
    return List.generate(32, (_) => rand.nextInt(16).toRadixString(16)).join();
  }

  /// Fetches the current FCM token and (re)registers it with the Pi.
  /// Safe to call repeatedly (e.g. on every successful connect) — the
  /// underlying MQTT publish is retained, so re-sending the same value is
  /// harmless.
  Future<void> registerToken() async {
    final token = await _getToken();
    if (token == null) return;
    final deviceId = await _deviceId();
    await _repository.registerFcmToken(deviceId, token);
  }

  /// Starts listening for FCM token rotation and republishes on change.
  void listenForRefresh() {
    _onTokenRefresh().listen((newToken) async {
      final deviceId = await _deviceId();
      await _repository.registerFcmToken(deviceId, newToken);
    });
  }

  /// Starts listening for FCM messages that arrive while the app is in the
  /// foreground (background/terminated delivery is handled automatically by
  /// Android and never reaches this callback). Missing title/body fields
  /// fall back to a generic label rather than showing a blank notification.
  void listenForForegroundMessages(void Function(String title, String body) onNotification) {
    _onMessage().listen((message) {
      final title = message.notification?.title ?? 'Greenhouse';
      final body = message.notification?.body ?? '';
      onNotification(title, body);
    });
  }
}
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd app && flutter test test/services/fcm_token_service_test.dart`
Expected: `All tests passed!`

- [ ] **Step 9: Commit**

```bash
git add app/pubspec.yaml app/pubspec.lock app/android/settings.gradle.kts \
        app/android/app/build.gradle.kts \
        app/android/app/src/main/AndroidManifest.xml \
        app/lib/repository/greenhouse_repository.dart \
        app/lib/services/fcm_token_service.dart \
        app/test/services/fcm_token_service_test.dart
git commit -m "feat: add FCM token registration service"
```

---

### Task 5: Wire FCM into app startup, retire redundant MQTT-driven notification

**Files:**
- Modify: `app/lib/main.dart`
- Modify: `app/lib/providers/connection_provider.dart`
- Modify: `app/lib/app.dart`
- Modify: `app/lib/services/notification_service.dart` (remove now-dead `showAlert`)

**Interfaces:**
- Consumes: `FcmTokenService` (Task 4), `NotificationService.instance.showInfo(String title, String body)` (existing), `connectionStatusProvider` (existing, emits `ConnectionStatus`).

- [ ] **Step 1: Initialize Firebase in main.dart**

In `app/lib/main.dart`, change:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/app.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProviderScope(child: GreenhouseApp()));
}
```

to:

```dart
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/app.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp();
  runApp(const ProviderScope(child: GreenhouseApp()));
}
```

(`firebase_core` was already added to `app/pubspec.yaml` in Task 4, Step 1, so no further dependency changes are needed here.)

- [ ] **Step 2: Add the FcmTokenService provider**

In `app/lib/providers/connection_provider.dart`, add the import:

```dart
import 'package:greenhouse_app/services/fcm_token_service.dart';
```

and, after the existing `repositoryProvider` definition, add:

```dart

final fcmTokenServiceProvider = Provider(
    (ref) => FcmTokenService(ref.watch(repositoryProvider)));
```

- [ ] **Step 3: Replace the MQTT-driven notification wiring in app.dart with FCM**

In `app/lib/app.dart`, change:

```dart
  Future<void> _initNotifications() async {
    await NotificationService.instance.init();
    await NotificationService.instance.requestPermission();
    // Listen for incoming weather alerts and show local notifications
    _alertSub = ref.listenManual(weatherAlertsProvider, (_, next) {
      next.whenData((alert) => NotificationService.instance.showAlert(alert));
    });
  }
```

to:

```dart
  Future<void> _initNotifications() async {
    await NotificationService.instance.init();
    await NotificationService.instance.requestPermission();
  }

  void _initFcm() {
    final fcm = ref.read(fcmTokenServiceProvider);
    fcm.listenForRefresh();
    fcm.listenForForegroundMessages(NotificationService.instance.showInfo);

    // Re-register on every successful connect (harmless/idempotent — the
    // underlying publish is retained) so a token obtained before the first
    // connection isn't lost.
    _alertSub = ref.listenManual(connectionStatusProvider, (_, next) {
      next.whenData((status) {
        if (status == ConnectionStatus.local || status == ConnectionStatus.remote) {
          ref.read(fcmTokenServiceProvider).registerToken();
        }
      });
    });
  }
```

Change the `initState()` call from:

```dart
    _initNotifications();
```

to:

```dart
    _initNotifications();
    _initFcm();
```

(`_alertSub`'s field declaration and its `.close()` in `dispose()` stay exactly as they are — it's being reused for the new connection-status subscription instead of the old alert subscription.)

- [ ] **Step 4: Remove the now-dead `showAlert` from NotificationService**

In `app/lib/services/notification_service.dart`, remove the `showAlert` method entirely (the `showAlert(WeatherAlert alert)` method, roughly lines 37-58) and remove the now-unused import `import 'package:greenhouse_app/models/weather_alert.dart';`. `showInfo` (used by the new FCM foreground handler) stays as-is.

- [ ] **Step 5: Verify the app still analyzes cleanly**

Run: `cd app && flutter analyze`
Expected: `No issues found!`

- [ ] **Step 6: Verify existing tests still pass**

Run: `cd app && flutter test`
Expected: all tests pass (no regressions from the `app.dart`/`notification_service.dart` changes — neither file has direct unit test coverage today, so this is a check that nothing else broke, not new coverage for this task).

- [ ] **Step 7: Commit**

```bash
git add app/lib/main.dart app/lib/providers/connection_provider.dart \
        app/lib/app.dart app/lib/services/notification_service.dart
git commit -m "feat: wire FCM into app startup, retire redundant MQTT notification path"
```

**Manual bench-test step (cannot be verified from this dev sandbox):** once a real Firebase project + `google-services.json` + `/etc/greenhouse/firebase-service-account.json` exist, verify end-to-end: trigger a weather alert (or use the app's existing rule-testing flow) with the app fully closed/force-stopped, and confirm a real Android notification arrives.
