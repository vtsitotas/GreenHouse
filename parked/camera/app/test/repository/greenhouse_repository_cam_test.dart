// PARKED — camera repository tests.
//
// These exercised GreenhouseRepository's camera surface (camStatus,
// fetchEventPhoto, startLive/stopLive, live-frame reassembly), which was
// removed from app/lib/repository/greenhouse_repository.dart when the camera
// was parked. They do not compile as-is and are not run by CI; they are kept
// verbatim so restoring the feature restores its tests with it. See
// parked/camera/README.md for the restore steps.

import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/weather_events.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';

// `repo`, `conn`, `eventsCtrl` and `_config` come from the setUp() block in
// app/test/repository/greenhouse_repository_test.dart — fold these back into
// that file rather than running this one standalone.

void main() {
  test('camStatus stream emits parsed CamStatus from a CamStatusRaw event', () async {
    final future = repo.camStatus.first;
    eventsCtrl.add(const CamStatusRaw('{"online": true, "last_seen": 1700000000.0, "ip": "192.168.1.50", "last_event": null}'));
    final status = await future;
    expect(status.online, isTrue);
    expect(status.ip, '192.168.1.50');
  });

  test('fetchEventPhoto requests over MQTT and reassembles chunked response', () async {
    repo.connect(_config);
    final resultFuture = repo.fetchEventPhoto('evt1');

    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/cam/event/request', captureAny()),
    ).captured.single as String;
    final request = jsonDecode(capturedPayload) as Map<String, dynamic>;
    expect(request['event_id'], 'evt1');
    final reqId = request['id'] as String;

    final bytes = utf8.encode('fake-jpeg-bytes');
    final b64 = base64Encode(bytes);
    eventsCtrl.add(CamEventChunkRaw(reqId, jsonEncode({'chunk': 0, 'total': 1, 'data': b64})));

    final result = await resultFuture;
    expect(result, bytes);
  });

  test('fetchEventPhoto reassembles multiple chunks in order', () async {
    repo.connect(_config);
    final resultFuture = repo.fetchEventPhoto('evt1');
    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/cam/event/request', captureAny()),
    ).captured.single as String;
    final reqId = (jsonDecode(capturedPayload) as Map<String, dynamic>)['id'] as String;

    final part1 = base64Encode(utf8.encode('AAA'));
    final part2 = base64Encode(utf8.encode('BBB'));
    eventsCtrl.add(CamEventChunkRaw(reqId, jsonEncode({'chunk': 1, 'total': 2, 'data': part2})));
    eventsCtrl.add(CamEventChunkRaw(reqId, jsonEncode({'chunk': 0, 'total': 2, 'data': part1})));

    final result = await resultFuture;
    expect(utf8.decode(result!), 'AAABBB');
  });

  test('fetchEventPhoto returns null on a camera_unreachable error', () async {
    repo.connect(_config);
    final resultFuture = repo.fetchEventPhoto('evt1');
    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/cam/event/request', captureAny()),
    ).captured.single as String;
    final reqId = (jsonDecode(capturedPayload) as Map<String, dynamic>)['id'] as String;

    eventsCtrl.add(CamEventChunkRaw(reqId, jsonEncode({'error': 'camera_unreachable'})));

    expect(await resultFuture, isNull);
  });

  test('startLive publishes to greenhouse/cam/live/start', () async {
    await repo.startLive();
    verify(() => conn.publishRaw('greenhouse/cam/live/start', any())).called(1);
  });

  test('stopLive publishes to greenhouse/cam/live/stop', () async {
    await repo.stopLive();
    verify(() => conn.publishRaw('greenhouse/cam/live/stop', any())).called(1);
  });

  test('liveFrames emits reassembled frame bytes from chunked live-frame events', () async {
    final future = repo.liveFrames.first;
    final data = utf8.encode('one-frame-jpeg');
    eventsCtrl.add(CamLiveFrameChunkRaw(jsonEncode({
      'frame_id': 1, 'chunk': 0, 'total': 1, 'data': base64Encode(data),
    })));
    expect(await future, data);
  });

  test('liveFrames evicts the oldest incomplete in-flight frame instead of leaking it forever', () async {
    // Repository's live-frame reassembly bounds itself to 2 simultaneously
    // in-flight incomplete frame_ids (see _maxInFlightLiveFrames). A third
    // distinct incomplete frame must evict the oldest rather than let it
    // accumulate in memory for the rest of a lossy live session.
    final emitted = <List<int>>[];
    final sub = repo.liveFrames.listen(emitted.add);

    Map<String, dynamic> chunk(int frameId, int chunkIdx, int total, String data) => {
          'frame_id': frameId,
          'chunk': chunkIdx,
          'total': total,
          'data': base64Encode(utf8.encode(data)),
        };

    // Frame 1 and 2 each get only their first chunk (as if the second was
    // lost in transit) -- this fills the 2-frame in-flight bound.
    eventsCtrl.add(CamLiveFrameChunkRaw(jsonEncode(chunk(1, 0, 2, 'a1'))));
    eventsCtrl.add(CamLiveFrameChunkRaw(jsonEncode(chunk(2, 0, 2, 'b1'))));
    // Frame 3 is a genuinely new frame_id arriving while both slots are
    // full, so it evicts frame 1 (the oldest) to stay within the bound.
    eventsCtrl.add(CamLiveFrameChunkRaw(jsonEncode(chunk(3, 0, 2, 'c1'))));
    await Future(() {});

    // Frame 2 was never evicted, so its remaining chunk still completes it.
    eventsCtrl.add(CamLiveFrameChunkRaw(jsonEncode(chunk(2, 1, 2, 'b2'))));
    await Future(() {});
    expect(emitted.length, 1);
    expect(utf8.decode(emitted.last), 'b1b2');

    // Frame 1's remaining chunk arrives late. Because frame 1's original
    // buffer (holding chunk 0's data) was evicted, this starts a brand new
    // buffer missing chunk 0 -- it never completes, and frame 1's data is
    // gone rather than lingering in _liveFrameBuffers indefinitely.
    eventsCtrl.add(CamLiveFrameChunkRaw(jsonEncode(chunk(1, 1, 2, 'a2'))));
    await Future(() {});
    expect(emitted.length, 1);

    // Frame 3 is still tracked and completes normally.
    eventsCtrl.add(CamLiveFrameChunkRaw(jsonEncode(chunk(3, 1, 2, 'c2'))));
    await Future(() {});
    expect(emitted.length, 2);
    expect(utf8.decode(emitted.last), 'c1c2');

    await sub.cancel();
  });
}
