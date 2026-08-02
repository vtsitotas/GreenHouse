# Parked — ESP32-CAM / motion detection

The camera never worked well enough on real hardware to keep in the prototype,
so it was **parked** here rather than deleted: nothing under `parked/` is
built, installed, flashed, or tested by anything in this repo. The app has no
Camera tab, `pi/install.sh` no longer installs a cam-bridge service, and CI
doesn't collect these tests.

Everything is still here, unmodified, and the git history is intact (the files
were moved with `git mv`, so `git log --follow` works on each one).

---

## What's in here

| Path | Was |
|---|---|
| `firmware/cam_esp32/cam_esp32.ino` | `firmware/cam_esp32/` — ESP32-CAM sketch (HTTP snapshot/stream + SD storage) |
| `pi/scripts/cam_bridge.py` | `pi/scripts/` — polls the camera, runs motion detection, relays over MQTT |
| `pi/shared/motion.py` | `pi/shared/` — PIL frame-differencing helper |
| `pi/shared/cam_store.py` | `pi/shared/` — on-disk event/photo store |
| `pi/systemd/greenhouse-cam-bridge.service` | `pi/systemd/` |
| `pi/cam_token.txt.example` | `pi/` — shared token, must match `CAM_TOKEN` in the flashed firmware |
| `pi/tests/test_cam_bridge.py`, `test_cam_store.py`, `test_motion.py` | `pi/tests/` |
| `app/lib/screens/camera/camera_screen.dart` | `app/lib/screens/camera/` |
| `app/lib/providers/camera_provider.dart` | `app/lib/providers/` |
| `app/lib/models/cam_event.dart`, `cam_status.dart` | `app/lib/models/` |
| `app/test/...` | the matching widget/model tests |
| `app/test/repository/greenhouse_repository_cam_test.dart` | extracted from `app/test/repository/greenhouse_repository_test.dart` |
| `app/test/connection/mqtt_connection_cam_test.dart` | extracted from `app/test/connection/mqtt_connection_test.dart` |
| `docs/12-camera-motion.md` | `docs/technical/12-camera-motion.md` |

The design record stays in `docs/superpowers/` (`2026-07-10-esp32-cam-integration-design.md`,
`2026-07-11-esp32-cam-integration.md`) — those are history, not live work.

## What was edited out of shared files

These files are shared with live features, so parking the camera meant deleting
lines from them rather than moving a whole file. To restore, put them back:

**App**
- `app/lib/app.dart` — the `CameraScreen` import and the `/camera` `GoRoute`.
- `app/lib/screens/shell_screen.dart` — `/camera` in `_routes` and the
  `Icons.videocam` `NavigationDestination` (they're positional — the route list
  and the destination list must stay in the same order).
- `app/lib/models/weather_events.dart` — `CamStatusRaw`, `CamEventChunkRaw`,
  `CamLiveFrameChunkRaw`.
- `app/lib/connection/mqtt_connection.dart` — the `isCamStatusTopic` /
  `isCamEventResponseTopic` / `extractCamEventReqId` / `isCamLiveFrameTopic`
  helpers and their three branches in `_route()`. Also drop `p[1] == 'cam'`
  from `isSensorTopic`'s exclusion list, which is only there to stop a
  re-enabled cam bridge's JSON from being parsed as a sensor reading while the
  routing branches are gone.
- `app/lib/repository/greenhouse_repository.dart` — `_camStatusCtrl`,
  `_camEventChunkCtrl`, `_camLiveFrameCtrl`, `_liveFrameBuffers` +
  `_maxInFlightLiveFrames`, the three `_handle()` branches,
  `_handleLiveFrameChunk()`, `camStatus`, `liveFrames`, `fetchEventPhoto()`,
  `startLive()`, `stopLive()`.
- `app/lib/models/notification_settings.dart` — the `motionAlert` field
  (JSON key `motion_alert`), plus its `SwitchListTile` ("Camera motion alerts",
  key `alert-settings-motion-switch`) in `app/lib/screens/weather/weather_screen.dart`.
- `app/lib/models/weather_alert.dart` — `case 'motion': return '📷 Motion Detected';`
- `app/pubspec.yaml` — the `flutter_mjpeg` dependency (LAN live view only).

**Pi**
- `pi/install.sh` — the `python3-pil` apt package, the `cam_token.txt`
  provisioning block, the `greenhouse-cam-bridge.service` copy/enable/restart.
  It now actively removes the unit if a previously-installed Pi still has it;
  delete that block when restoring.
- `pi/scripts/weather.py` — `motion_alert` in `_pull_notification_settings()`
  and `load_notification_settings()`.
- `.github/workflows/ci.yml` — `Pillow` in the pip install line (only
  `motion.py` and its tests needed it).

On a Pi that was installed while the camera still shipped, the next
`install.sh` run disables and deletes `greenhouse-cam-bridge.service`. It does
**not** delete `/etc/greenhouse/cam_token.txt` — that file is left in place,
unread by anything, and is only useful again if the camera comes back.

## Restoring

`git mv` the files back to the paths in the table above, re-apply the shared-file
edits listed here, fold the two extracted test files back into their originals,
and re-run `flutter analyze && flutter test` plus `pytest pi/tests/`. The commit
that parked the camera has the exact diff if you'd rather revert it wholesale.

## Why it was parked (2026-08-02)

Bench testing never got the camera to a useful state — the firmware compiled and
was ready to flash but stayed untested on real hardware, and the feature carried
a whole vertical slice of always-on cost: an extra systemd service polling on
the Pi, three MQTT topic families in the app's routing, a chunked base64
photo-transfer protocol, and two dependencies (`flutter_mjpeg`, Pillow) used for
nothing else.
