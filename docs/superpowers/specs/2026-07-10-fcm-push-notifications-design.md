# FCM Push Notifications — Design Spec

**Date:** 2026-07-10
**Status:** Approved, ready for implementation planning

## Background

Weather alerts (`pi/scripts/weather.py` — rule triggers, forecast-based alerts,
frost alerts) currently reach the phone only via a raw MQTT publish to
`greenhouse/weather/alert`, non-retained. The app shows a local notification
(`NotificationService.showAlert`, via `flutter_local_notifications`) only when
`app.dart`'s `_alertSub` is actively listening — which only happens while the
app process is alive and its MQTT connection (LAN or the HiveMQ remote
fallback) is up. There is no Firebase/FCM, no `workmanager` or background
service in `app/pubspec.yaml`, and no background isolate anywhere in the app.
Concretely: if the app is fully closed or force-stopped when an alert fires,
the notification is silently lost — on LAN or remote alike, since the gap is
"is the app running," not which transport it would have used.

This was surfaced while scoping a future ESP32-CAM feature (motion alerts
would inherit the identical gap) but is a real, standalone defect in the
existing weather-alert path, worth fixing on its own.

Every commercial IoT/camera app (Ring, Nest, Xiaomi Home, Tuya/SmartLife,
Blink, etc.) solves "notify while the app is fully closed" the same way: a
cloud-reachable backend calls FCM (Android) / APNs (iOS) on the app's behalf.
This project already has the backend half of that shape — the Pi already
talks to an external cloud service (HiveMQ Cloud) for the equivalent problem
on the MQTT side. Adding an FCM call from the Pi is comparable in scope to
`pi/scripts/hivemq_bridge.py`, not a disproportionate addition.

## Goals

1. Weather alerts (all three existing trigger sites in `weather.py`) reach the
   phone as a real OS notification even when the app is fully closed or
   force-stopped, on Android.
2. Support more than one registered phone/device per Pi unit.
3. Reuse the existing in-app MQTT-driven state (Weather screen's live alert
   banner) unchanged — FCM is an added delivery channel for OS-level
   notifications, not a replacement for the app's live connected state.
4. Land a `send_push()` helper that a future camera motion-alert feature can
   call directly, without needing to touch this design again.

## Non-goals

- No camera/motion-alert integration — this project only builds the reusable
  `pi/shared/push.py` helper and the weather-alert call sites; a future
  camera feature becomes another caller.
- No token expiry/cleanup UI or explicit device de-registration flow. A
  stale/invalid token simply fails silently per-token when sending (logged,
  skipped) — acceptable at this project's scale, not solved here.
- No iOS work — iOS is already completely untested for this app (see
  project memory); this spec is Android-only.
- No change to the existing MQTT alert publish (`greenhouse/weather/alert`)
  — it keeps firing exactly as today, for the Weather screen's in-app banner.

## Architecture

The Pi keeps its existing behavior (evaluate rules, decide an alert fired,
`mqtt_publish('greenhouse/weather/alert', ...)`) unchanged, and gains one new
step: also calling Firebase Cloud Messaging so the phone receives a real OS
notification regardless of whether the app is open. FCM is layered on top of,
not instead of, the existing MQTT-driven live state.

### Token registration (multi-device)

Each app install generates a stable UUID once on first run, persisted via the
already-used `flutter_secure_storage`. It publishes its current FCM token
**retained** to a per-device topic, `greenhouse/app/fcm_token/<device-uuid>`.
On `FirebaseMessaging.instance.onTokenRefresh`, it republishes to the same
topic, overwriting the old retained value.

The Pi rediscovers the full current device set each cycle by subscribing to
the wildcard and capturing every currently-retained value (not just one):

```
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'greenhouse/app/fcm_token/+' -v -W 3
```

This generalizes the existing `_pull_location_from_mqtt()` pattern in
`weather.py` (which uses `mosquitto_sub -C 1 -W 2` against a single retained
topic) to a wildcard subscribe that returns as many retained messages as
currently exist, one per registered device. Each line is `topic payload`;
the device UUID is parsed from the topic suffix, the payload is the token.

This needs **no new file or database on the Pi** — the broker's own retained-
message store is the persistent registry. New devices are picked up on the
next poll after they publish; token refreshes overwrite in place; there is no
separate add/remove bookkeeping to maintain.

### Pi-side send helper

New `pi/shared/push.py` (alongside the existing `pi/shared/history_query.py`
— this is where cross-service shared code already lives), exposing:

```python
def send_push(title: str, body: str) -> None:
    ...
```

It resolves the current token set via the wildcard `mosquitto_sub` call
above, then calls `firebase_admin.messaging.send()` once per token, catching
and logging failures **per token** so one stale/invalid token never blocks
delivery to the rest. Requires a Firebase service-account key on the Pi
(`/etc/greenhouse/firebase-service-account.json`, root-readable only, same
handling as the existing TLS certs/passwd files).

`weather.py`'s three existing alert call sites (rule trigger, forecast-based
alert, frost alert) each gain one added call to `push.send_push(alert['title'],
alert['message'])` right alongside their existing `mqtt_publish(
'greenhouse/weather/alert', ...)` call. The MQTT publish is unchanged.

### App-side

- Add `firebase_core` + `firebase_messaging` to `app/pubspec.yaml`, plus the
  Firebase project's `google-services.json` and the
  `com.google.gms.google-services` Gradle plugin.
- On startup: initialize Firebase, get the FCM token, generate/load the
  per-install device UUID, publish the retained registration topic described
  above. Re-publish on `onTokenRefresh`.
- `FirebaseMessaging.onMessage` (foreground delivery) routes into the same
  `NotificationService` used today, so the in-app-open experience is
  unchanged in substance.
- Background/terminated delivery is handled automatically by Android from
  the FCM message's `notification` payload — no app code runs for that case.
- The Android notification channel ID for FCM messages is set to match the
  existing `greenhouse_weather` channel (`AndroidManifest.xml` meta-data
  `default_notification_channel_id`), so a closed-app notification looks
  identical to today's in-app one.
- **Removed:** `app.dart`'s `_alertSub = ref.listenManual(weatherAlertsProvider,
  ...) → NotificationService.showAlert()` wiring — this is now redundant with
  FCM's own foreground handler doing the equivalent job, and keeping both
  would double-fire a notification for the same alert while the app is open.
  `weatherAlertsProvider`'s underlying MQTT stream is otherwise unchanged —
  `weather_screen.dart` still uses it directly for its in-app live banner,
  which is unrelated to OS-level notifications.

## Error Handling

- A send failure for one device token (expired/unregistered/invalid) is
  caught and logged inside `send_push()`'s per-token loop; it does not raise,
  does not block sending to other registered tokens, and does not affect the
  existing MQTT publish.
- If no tokens are currently registered (e.g. fresh install, no app has ever
  published one), `send_push()` is a no-op — alerts still publish over MQTT
  as today.
- `weather.py`'s existing `try/except` pattern around each alert call site is
  extended to wrap the new `send_push()` call the same way it already wraps
  `mqtt_publish` failures — a push failure never prevents the rule evaluation
  loop from continuing.

## Testing

Scoped proportionally to a thesis project:

- `pi/tests/test_push.py` (new): parsing the `mosquitto_sub -v` wildcard
  output into a device-uuid → token mapping (including zero, one, and
  multiple registered devices); `send_push()` calling
  `firebase_admin.messaging.send()` once per resolved token; a simulated
  per-token failure not raising and not blocking the remaining tokens.
- `pi/tests/test_weather_rules.py`: each of the three alert call sites also
  invokes `send_push()` with the alert's title/message (mocked, not a real
  Firebase call).
- App-side: a test for the retained-token-publish logic (UUID
  generation/persistence, publish-on-refresh) and for the foreground
  `onMessage` handler invoking `NotificationService`, using fakes — matching
  the existing test style under `app/test/providers/`. Real end-to-end FCM
  delivery (does a background/terminated notification actually arrive) is a
  manual bench-test step once a real Firebase project exists, the same way
  the mesh relay firmware's real behavior was only bench-validated on actual
  hardware, not proven in the dev sandbox.

## Known Risks (bench-test items, not solved at design time)

- **`firebase-admin` on the Pi Zero W:** the Python package's compatibility
  with the Pi's ARMv6/Trixie environment is unverified. If installation or
  runtime behavior proves too heavy, a documented fallback is calling FCM's
  HTTP v1 REST API directly with a manually-signed service-account JWT (more
  code, lighter dependency footprint) — reach for this only if the bench
  test actually fails, not as a parallel implementation built alongside the
  primary approach.
- **Manual one-time setup:** creating the actual Firebase project, adding the
  Android app to it, downloading `google-services.json`, and generating the
  service-account key are manual console steps outside of code — these need
  to happen before the Pi-side or app-side code can be bench-tested
  end-to-end.

## Follow-up (explicitly deferred)

- Camera motion-alert push, once the ESP32-CAM feature is built — a future
  caller of `pi/shared/push.py::send_push()`, not part of this project.
- Device token cleanup/de-registration (e.g. on app uninstall or explicit
  unpair).
- iOS push (APNs) — iOS is untested for this app generally, out of scope
  here.
