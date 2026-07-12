import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/models/notification_settings.dart';
import 'package:greenhouse_app/models/sensor_reading.dart';
import 'package:greenhouse_app/models/weather_events.dart';
import 'package:greenhouse_app/models/weather_rule.dart';
import 'package:greenhouse_app/models/cam_status.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';

class MockConnection extends Mock implements GreenhouseConnection {}

const _config = ConnectionConfig(
  lanHost: 'greenhouse.local',
  remoteHost: '',
  port: 9001,
  tlsFingerprint: 'abc',
  username: 'app',
  password: 'pass',
  remoteUsername: '',
  remotePassword: '',
);

void main() {
  setUpAll(() {
    registerFallbackValue(const ConnectionConfig(
      lanHost: '', remoteHost: '', port: 9001,
      tlsFingerprint: '', username: '', password: '',
      remoteUsername: '', remotePassword: '',
    ));
  });

  late MockConnection conn;
  late StreamController<dynamic> eventsCtrl;
  late StreamController<ConnectionStatus> statusCtrl;
  late GreenhouseRepository repo;

  setUp(() {
    conn = MockConnection();
    eventsCtrl = StreamController<dynamic>.broadcast();
    statusCtrl = StreamController<ConnectionStatus>.broadcast();
    when(() => conn.events).thenAnswer((_) => eventsCtrl.stream);
    when(() => conn.status).thenAnswer((_) => statusCtrl.stream);
    when(() => conn.connect(any())).thenAnswer((_) async {});
    when(() => conn.disconnect()).thenAnswer((_) async {});
    when(() => conn.sendCommand(any(), any())).thenAnswer((_) async {});
    when(() => conn.publishRaw(any(), any(), retain: any(named: 'retain')))
        .thenAnswer((_) async {});
    repo = GreenhouseRepository(connection: conn);
  });

  tearDown(() {
    eventsCtrl.close();
    statusCtrl.close();
    repo.disconnect();
  });

  test('aggregates sensor readings by zone and metric', () async {
    repo.connect(_config);
    // `readings` yields a cached snapshot immediately on subscribe, before any
    // pushed event is processed, so `.first` alone would race the event.
    // Wait for a snapshot that actually reflects the pushed reading instead.
    final future = repo.readings.firstWhere(
        (m) => m['zone1']?['air/temperature'] != null);
    // Let the async* generator's execution reach its `yield* _readingsCtrl.stream`
    // subscription before pushing — otherwise this broadcast-stream update fires
    // into the race window before anyone is listening for it, and is lost.
    await Future(() {});
    eventsCtrl.add(SensorReading(zone: 'zone1', metric: 'air/temperature', value: 25.0, receivedAt: DateTime.now()));
    final snapshot = await future;
    expect(snapshot['zone1']?['air/temperature'], 25.0);
  });

  test('fetchHistoryViaMqtt requests over MQTT and resolves the matching response', () async {
    repo.connect(_config);

    final resultFuture = repo.fetchHistoryViaMqtt(
        zone: 'zone1', kind: 'zone', metric: 'air_temperature', hours: 24);

    // Let the request's publishRaw() call land so we can read the id it used.
    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/history/request', captureAny(), retain: any(named: 'retain')),
    ).captured.single as String;
    final requestId = (jsonDecode(capturedPayload) as Map<String, dynamic>)['id'] as String;

    eventsCtrl.add(HistoryResponseRaw(
        requestId, jsonEncode({'zone': 'zone1', 'metric': 'air_temperature', 'points': [[1000, 20.0, 19.0, 21.0]]})));

    final result = await resultFuture;
    expect(result?['zone'], 'zone1');
    expect(result?['points'], [[1000, 20.0, 19.0, 21.0]]);
  });

  test('fetchHistoryViaMqtt sends since/until instead of hours for a custom range', () async {
    repo.connect(_config);

    final resultFuture = repo.fetchHistoryViaMqtt(
      zone: 'zone1',
      kind: 'zone',
      metric: 'air_temperature',
      since: DateTime.fromMillisecondsSinceEpoch(1000000),
      until: DateTime.fromMillisecondsSinceEpoch(2000000),
    );

    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/history/request', captureAny(), retain: any(named: 'retain')),
    ).captured.single as String;
    final request = jsonDecode(capturedPayload) as Map<String, dynamic>;
    expect(request['since'], 1000);
    expect(request['until'], 2000);
    expect(request.containsKey('hours'), isFalse);

    eventsCtrl.add(HistoryResponseRaw(request['id'] as String, jsonEncode({'points': <List<dynamic>>[]})));
    await resultFuture;
  });

  test('fetchHistoryViaMqtt still sends hours (not since/until) for a normal rolling window', () async {
    repo.connect(_config);

    final resultFuture = repo.fetchHistoryViaMqtt(
        zone: 'zone1', kind: 'zone', metric: 'air_temperature', hours: 168);

    await Future(() {});
    final capturedPayload = verify(
      () => conn.publishRaw('greenhouse/history/request', captureAny(), retain: any(named: 'retain')),
    ).captured.single as String;
    final request = jsonDecode(capturedPayload) as Map<String, dynamic>;
    expect(request['hours'], 168);
    expect(request.containsKey('since'), isFalse);
    expect(request.containsKey('until'), isFalse);

    eventsCtrl.add(HistoryResponseRaw(request['id'] as String, jsonEncode({'points': <List<dynamic>>[]})));
    await resultFuture;
  });

  test('merges node status and battery into same nodeId entry', () async {
    repo.connect(_config);
    // Same cached-snapshot race as above — wait for the merged state instead
    // of assuming `.first` lines up with a specific pushed event.
    final future = repo.nodes.firstWhere((m) => m['node1']?.batteryPercent != null);
    // Same race as above — give the generator a tick to subscribe to the live
    // stream before pushing, or the update is lost to the broadcast controller.
    await Future(() {});
    eventsCtrl.add(NodeStatus.fromMqttStatus('node1', 'online'));
    eventsCtrl.add(NodeStatus.fromMqttBattery('node1', '75.0'));
    final nodes = await future;
    expect(nodes['node1']?.isOnline, isTrue);
    expect(nodes['node1']?.batteryPercent, 75.0);
  });

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

  test('publishNotificationSettings retains the message', () async {
    await repo.publishNotificationSettings(
      const NotificationSettings(frostForecast: false, dailySummary: true),
    );

    verify(() => conn.publishRaw(
          'greenhouse/settings/notifications',
          '{"frost_forecast":false,"daily_summary":true,"motion_alert":true}',
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
}
