# ESP32-CAM Integration — Design Spec

**Date:** 2026-07-10
**Status:** Approved, ready for implementation planning (MVP section only — Phase 2 is documented future work, not planned yet)

## Background

The greenhouse currently has no visual monitoring — only sensor readings
(temp/humidity/soil/etc via the ESP-NOW mesh) and weather data. This project
adds a single ESP32-CAM module (hardware already in hand, firmware not yet
flashed) to provide:

1. A live camera view, both on the home LAN and remotely.
2. Motion-detection alerts with an attached photo, delivered through the
   existing push-notification pipeline (`pi/shared/push.py::send_push()`,
   built in the FCM push notifications project — see
   `2026-07-10-fcm-push-notifications-design.md`).
3. A camera online/offline status indicator alongside the existing
   dashboard/zone cards.

This feature was originally floated in the previous session but paused once
the FCM push-notification gap was discovered — motion alerts would otherwise
have inherited the same "lost while app is closed" defect. That defect is now
fixed, clearing the way for this design.

Two live-view experiences are in scope conceptually, but only one is planned
for implementation now:

- **LAN view** (this spec, MVP): the camera's own MJPEG web server, viewed
  directly — genuinely smooth (~10-20fps), no Pi involvement.
- **Remote view, MVP tier**: an MQTT-relayed low-fps fallback (~1-3fps,
  "refreshing snapshot" rather than smooth video) — built now, since it
  reuses existing MQTT/HiveMQ infrastructure with no new external
  dependencies.
- **Remote view, Phase 2** (documented at the end of this spec, **not**
  planned/implemented in this cycle): a WebRTC-based real live stream,
  requiring a new TURN relay and a CPU-feasibility bench test on the Pi Zero
  W before it can even be scoped properly. Deliberately deferred to its own
  future brainstorm → spec → plan cycle.

## Goals (MVP)

1. View the camera live over the LAN, directly from the camera's own web
   server — no Pi in the path.
2. View the camera "live" while away from home, via a low-fps MQTT-relayed
   fallback, only while the app has the screen open (no continuous
   background streaming).
3. Motion detection (server-side frame-diffing on the Pi, not the camera)
   fires a real push notification (reusing `send_push()`) with a
   fetch-on-tap photo, appearing in the same alerts/history surface as
   weather and rule alerts.
4. A camera online/offline + last-motion-event status card, consistent with
   the existing dashboard card style.
5. Motion-event photos are retained for 7 days then auto-pruned.

## Non-goals (this cycle)

- Multiple cameras / per-zone camera mapping — single camera only.
- WebRTC / smooth remote video — see "Phase 2" below; documented, not built.
- Any always-on continuous remote frame push — remote live view is strictly
  on-demand, only while the screen is open.
- A raw-footage rolling backup independent of motion events (e.g. the camera
  recording everything to its own SD card regardless of detected motion) —
  the camera's SD card in this design is used *only* for Pi-flagged motion
  events, not a general recording buffer.
- iOS — untested for this app generally (see project memory), out of scope
  here too.

## Architecture

```
ESP32-CAM (LAN only — no internet/HiveMQ access, ever)
  ├─ MJPEG web server (stock-style /stream endpoint) → LAN live view, direct
  ├─ Periodic snapshot POST → Pi (http://<pi>:8080/cam/frame, every ~3s)
  ├─ SD card: stores ONLY Pi-flagged motion-event frames, keyed by event_id
  └─ Small HTTP API: GET /event/<event_id> (serve), DELETE /event/<event_id> (prune)

Pi (new pi/scripts/cam_bridge.py, same shape as weather.py/recorder.py)
  ├─ Motion loop: grayscale-diff each incoming snapshot vs. the previous
  │  in-memory frame; above threshold → assign event_id, reply to the
  │  camera's POST with "save:<event_id>"; log lightweight metadata only
  │  (event_id, timestamp, diff score — no image bytes) to a small SQLite
  │  table alongside the existing recorder DB
  ├─ Alert: calls push.send_push() exactly like a weather/rule alert — text
  │  only ("Motion detected — 14:32"), respecting a new per-category
  │  notify toggle in greenhouse/settings/notifications (motion_alert,
  │  alongside the existing frost_forecast/daily_summary keys)
  ├─ Event photo serving: on greenhouse/cam/event/request (payload:
  │  event_id), fetches GET http://<camera>/event/<id> and relays the bytes
  │  back on greenhouse/cam/event/response/<id> (base64-chunked, mirroring
  │  the existing greenhouse/history/request → greenhouse/history/response/
  │  shape in recorder.py); a direct Pi HTTP endpoint is used instead when
  │  the app is LAN-local, mirroring history's existing LAN-vs-remote split
  ├─ Live relay (MQTT fallback tier): app publishes greenhouse/cam/live/start
  │  / .../stop; while active, Pi polls the camera's /stream at ~1-3fps and
  │  republishes frames on greenhouse/cam/live/frame; auto-stops after 2
  │  minutes without a keep-alive so a crashed app can't leave it polling
  │  forever
  ├─ Pruning: daily task — for every event older than 7 days, DELETE
  │  /event/<id> on the camera, then delete the Pi's own metadata row (only
  │  after the camera confirms, so a temporarily-unreachable camera just
  │  retries next cycle rather than losing the metadata record)
  └─ Status/heartbeat: last successful /cam/frame POST timestamp = camera
     online; silence for >2x the expected interval = offline; published
     retained on greenhouse/cam/status ({online, last_seen, last_event})
     on every change, mirroring the existing retained-status pattern used
     for rules/notifications settings, so the app gets current state
     immediately on connect without polling

App (new Camera entry in shell_screen.dart, alongside dashboard/control/
     devices/history/weather)
  ├─ Status card: online/offline + last-motion thumbnail/timestamp — same
  │  visual language as existing zone cards
  ├─ Live view: on LAN, loads the camera's /stream URL directly (Pi tells
  │  the app the camera's current LAN IP, captured server-side from
  │  snapshot POST source addresses — no phone-side mDNS resolution
  │  needed); remote, sends live/start + live/stop over MQTT and renders
  │  the relayed frames, with a visible "refreshing ~1x/sec" indicator so
  │  the two-tier quality difference is communicated, not a silent surprise
  ├─ Events list: reverse-chronological motion events; tapping a push
  │  notification deep-links directly to that event, triggering the
  │  on-demand photo fetch described above
  └─ Degraded state: if the event photo fetch fails (camera offline), show
     "photo unavailable — camera offline" rather than an error — the alert
     already fired independently of whether the JPEG happens to be
     retrievable right now
```

### Why motion detection runs on the Pi, not the camera

The Pi already receives every periodic snapshot over HTTP to do the diff —
it needs no additional round trip to have the bytes in hand. Running
grayscale-diff logic on the Pi (ample RAM/CPU vs. the camera's constrained
OV2640 module) keeps the camera firmware close to the stock example, and
keeps all "smart" logic in Python alongside the rest of the automation
engine (`weather.py`'s rule evaluation), rather than inventing new
image-processing firmware from scratch.

### Why the camera's SD card is the photo store, not the Pi

Chosen explicitly over Pi-side storage during design (the Pi already has the
bytes in-hand at detection time, which would have been the simpler/more
resilient default). The camera's SD card is used instead because it's
already available hardware; the Pi keeps only lightweight metadata
(timestamp, diff score, event_id) and fetches the actual JPEG from the
camera on demand. Accepted tradeoff: event photos become unavailable if the
camera itself is offline/rebooting/its SD card fails, even though the Pi
already alerted on the event — handled as a graceful degraded state in the
app, not an error.

### MQTT topics (new)

| Topic | Direction | Purpose |
|---|---|---|
| `greenhouse/cam/event/request` | app → Pi | Request a specific event's photo (payload: `event_id`) |
| `greenhouse/cam/event/response/<id>` | Pi → app | Base64-chunked JPEG for that event |
| `greenhouse/cam/live/start` / `.../stop` | app → Pi | Begin/end the MQTT low-fps remote relay session |
| `greenhouse/cam/live/frame` | Pi → app | Relayed live frame while a session is active |
| `greenhouse/settings/notifications` (extended) | app → Pi | Existing topic gains a `motion_alert` boolean key alongside `frost_forecast`/`daily_summary` |
| `greenhouse/cam/status` | Pi → app | Retained: `{online, last_seen, last_event}`, republished on every change |

No new topic is needed for the camera→Pi snapshot path — that's plain LAN
HTTP (`POST http://<pi>:8080/cam/frame`), since the camera and Pi are always
on the same local network regardless of where the phone is.

## Error Handling

- Camera unreachable during event-photo fetch → Pi returns an explicit
  "unavailable" response rather than timing out silently; app shows the
  degraded state described above.
- Camera unreachable during pruning → Pi retries next cycle; metadata row is
  only deleted after the camera confirms the file is gone.
- No registered push tokens → `send_push()` is already a no-op per the FCM
  design; motion detection continues logging events regardless.
- Live-relay session with no keep-alive for 2 minutes → Pi auto-stops
  polling the camera, preventing a crashed/backgrounded app from leaving a
  permanent drain on the camera and Pi.
- A snapshot POST that the Pi fails to process (malformed JPEG, diff
  exception) is logged and discarded — never crashes the motion loop for
  the next frame.

## Testing

Scoped proportionally to a thesis project, matching the existing testing
style (`pi/tests/test_weather_rules.py`, `pi/tests/test_recorder.py`):

- `pi/tests/test_cam_bridge.py` (new): grayscale-diff threshold logic against
  synthetic frame pairs (no motion, clear motion, borderline); event_id
  generation; the "save:<id>" vs "discard" response logic; pruning selecting
  the correct set of expired events; the request/response photo-relay
  chunking logic (mocked camera HTTP calls).
- App-side: fake-backed tests for the Camera screen's LAN-vs-remote source
  switching, the live start/stop MQTT calls, and the degraded "photo
  unavailable" state — matching the existing style under
  `app/test/providers/` and `app/test/screens/`.
- **Hardware validation is a manual step, not something a subagent or CI can
  verify** — same situation as the still-unflashed mesh relay firmware (see
  project memory). Once the ESP32-CAM firmware is flashed, the manual
  checklist is: confirm `/stream` loads directly on LAN → confirm periodic
  snapshot POSTs land on the Pi → trigger real motion (wave a hand) →
  confirm a push notification arrives → confirm the event photo fetch
  works, including with the phone off WiFi (mobile data, exercising the
  remote/MQTT relay path) → confirm the status card flips to offline when
  the camera is unplugged.

## Phase 2 (future work, not planned/implemented this cycle) — WebRTC remote live streaming

Documented here in enough detail to resume from later, deliberately **not**
broken into implementation tasks yet — this is large enough to warrant its
own brainstorm → spec → plan cycle when the user is ready to commit to it.

**Structural decision:** the ESP32-CAM never speaks WebRTC itself — that's a
serious lift for a module with ~4MB PSRAM and no hardware video encoder.
Instead, the Pi ingests the camera's existing LAN MJPEG stream (the camera
firmware is completely unchanged from the MVP) and re-packages it as a
WebRTC video track.

```
ESP32-CAM (unchanged from MVP — same /stream endpoint)
        │  LAN HTTP
        ▼
Pi: aiortc (Python) wraps camera frames as a VideoStreamTrack, runs an
    RTCPeerConnection per remote viewing session
        │  Signaling (SDP offer/answer + ICE candidates) rides the existing
        │  MQTT/HiveMQ bridge — small text payloads, no new signaling
        │  server needed (greenhouse/cam/webrtc/offer, .../answer,
        │  .../ice/<from>)
        ▼
TURN relay (new external component) — needed because the zero-touch
  deployment model has no port-forwarding step; almost every home router
  will block direct P2P media, so STUN alone won't be sufficient
        ▼
App: flutter_webrtc renders the incoming track
```

**New components required:**

| Component | Role | Notes |
|---|---|---|
| `aiortc` (Pi) | Video track + peer connection | New Python dependency |
| `flutter_webrtc` (app) | Renders the track | New Flutter dependency |
| TURN relay | Relays media when direct P2P fails | New *external* infrastructure — self-hosted coturn (cheapest: a free-tier or ~$4-6/mo VPS) or a hosted TURN service (usage-based cost, zero maintenance) |
| MQTT (existing) | Signaling transport only | No new infra |

**Open risk that must be resolved first, before any protocol work:** the Pi
Zero W is single-core ARMv6 (~1GHz) with no hardware video encoder. Whether
it can software-encode a live WebRTC track in real time — concurrently with
Mosquitto, the HiveMQ bridge, the weather engine, and the SQLite recorder —
is unknown and must be bench-tested with a standalone aiortc script
(measuring actual achievable fps/resolution/CPU headroom) before the rest of
this phase is scoped in detail. If the number comes back too low, the
fallback is a lower resolution/fps target or a lighter re-packaging approach
rather than a full re-encode — not abandoning the feature outright.

**Security:** TURN credentials must be short-lived and HMAC-signed (the
standard "TURN REST API" convention), not a single static shared secret,
since the relay would otherwise be scrapable and abusable by strangers.

**Fallback behavior:** if WebRTC negotiation fails or times out (TURN
unreachable, Pi overloaded), the app falls back to the MVP's MQTT low-fps
relay tier automatically — WebRTC is a strictly-better upgrade path when
available, never a replacement that can leave the user with a dead screen.

## Follow-up (explicitly deferred)

- Phase 2 (WebRTC remote streaming) — its own future brainstorm/spec/plan
  cycle, starting with the Pi Zero W CPU-feasibility bench test above.
- Multiple cameras / per-zone mapping.
- A general raw-footage recording buffer on the camera's SD card,
  independent of motion-flagged events.
