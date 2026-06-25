import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/models/sensor_reading.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';

class MockConnection extends Mock implements GreenhouseConnection {}

const _config = ConnectionConfig(
  lanHost: 'greenhouse.local',
  tailscaleHost: '100.0.0.1',
  port: 9001,
  tlsFingerprint: 'abc',
  username: 'app',
  password: 'pass',
);

void main() {
  setUpAll(() {
    registerFallbackValue(const ConnectionConfig(
      lanHost: '', tailscaleHost: '', port: 9001,
      tlsFingerprint: '', username: '', password: '',
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
    repo = GreenhouseRepository(connection: conn);
  });

  tearDown(() {
    eventsCtrl.close();
    statusCtrl.close();
    repo.disconnect();
  });

  test('aggregates sensor readings by zone and metric', () async {
    repo.connect(_config);
    // Listen BEFORE adding the event — broadcast streams drop events with no listener.
    final future = repo.readings.first;
    eventsCtrl.add(SensorReading(zone: 'zone1', metric: 'air/temperature', value: 25.0, receivedAt: DateTime.now()));
    final snapshot = await future;
    expect(snapshot['zone1']?['air/temperature'], 25.0);
  });

  test('merges node status and battery into same nodeId entry', () async {
    repo.connect(_config);
    final future = repo.nodes.first;
    eventsCtrl.add(NodeStatus.fromMqttStatus('node1', 'online'));
    eventsCtrl.add(NodeStatus.fromMqttBattery('node1', '75.0'));
    await future; // first status event resolves the future
    // Battery arrives after; give it a tick then check current state via a second emission
    final nodesFuture = repo.nodes.first;
    eventsCtrl.add(NodeStatus.fromMqttBattery('node1', '75.0'));
    final nodes = await nodesFuture;
    expect(nodes['node1']?.isOnline, isTrue);
    expect(nodes['node1']?.batteryPercent, 75.0);
  });
}
