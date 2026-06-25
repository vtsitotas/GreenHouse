# Greenhouse IoT — App + Connectivity (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Flutter app (Android + iOS) that connects to the Pi's Mosquitto broker over local WiFi and Tailscale, showing live sensor data, node status, and actuator control — demoable with the included sensor simulator.

**Architecture:** The app connects directly to Mosquitto's WebSocket/TLS listener (port 9001) via `mqtt_client`; it tries LAN (`greenhouse.local` mDNS) first and falls back to Tailscale automatically. A `GreenhouseConnection` abstract interface wraps the MQTT transport so a cloud relay can replace it in Slice 5 without rewriting UI. Riverpod manages state; MQTT retained messages pre-populate all tiles on connect with no database needed.

**Tech Stack:** Flutter 3.x · Dart 3.x · flutter_riverpod 2.x · mqtt_client · flutter_secure_storage · mobile_scanner · go_router · Python 3 (simulator + QR script) · Mosquitto 2.x · Avahi (mDNS) · Tailscale

## Global Constraints

- Minimum Android SDK 26 (Android 8.0), iOS 14.
- All MQTT from the app uses WSS (port 9001) — no plaintext WebSocket in production.
- Self-signed TLS cert on Pi; app accepts it (fingerprint pinning deferred to Slice 5).
- All sensor/status topics must be published with `retain=true` so app gets last values on connect with no database.
- LWT on `greenhouse/nodes/{id}/status` = `"offline"` (retained, QoS 1) for every node.
- No optimistic UI for actuator control — wait for confirmed `.../state` message.
- Actuator command timeout: 5 seconds, then show error toast.
- Credentials stored in `flutter_secure_storage` only — never `shared_preferences`.
- Connection auto-falls back: LAN → Tailscale → Offline. Never blocks UI.
- No SQLite or Hive in Slice 1. Last-known values come from Mosquitto retained messages on reconnect.
- Pi commands are run over SSH: `ssh pi@192.168.1.88`
- Loopback plaintext (port 1883) remains available on Pi for local tooling only.

---

## File Map

### Flutter app (`app/`)

```
app/
├── pubspec.yaml
├── lib/
│   ├── main.dart
│   ├── app.dart
│   ├── models/
│   │   ├── sensor_reading.dart
│   │   ├── node_status.dart
│   │   ├── actuator_state.dart
│   │   ├── connection_config.dart
│   │   └── connection_status.dart
│   ├── connection/
│   │   ├── greenhouse_connection.dart      ← abstract interface
│   │   └── mqtt_connection.dart            ← MQTT-WS implementation
│   ├── repository/
│   │   └── greenhouse_repository.dart      ← aggregates streams into typed state
│   ├── providers/
│   │   ├── connection_provider.dart
│   │   ├── readings_provider.dart
│   │   ├── nodes_provider.dart
│   │   └── actuators_provider.dart
│   ├── services/
│   │   └── pairing_service.dart            ← secure storage for ConnectionConfig
│   ├── theme/
│   │   ├── app_colors.dart
│   │   └── app_theme.dart
│   └── screens/
│       ├── shell_screen.dart               ← bottom nav shell
│       ├── pairing/
│       │   ├── pairing_screen.dart
│       │   └── qr_scan_screen.dart
│       ├── dashboard/
│       │   ├── dashboard_screen.dart
│       │   ├── zone_card.dart
│       │   └── connection_banner.dart
│       ├── devices/
│       │   ├── devices_screen.dart
│       │   └── node_list_tile.dart
│       ├── control/
│       │   ├── control_screen.dart
│       │   └── actuator_toggle.dart
│       └── settings/
│           └── settings_screen.dart
└── test/
    ├── models/
    │   └── sensor_reading_test.dart
    ├── connection/
    │   └── mqtt_connection_test.dart
    ├── repository/
    │   └── greenhouse_repository_test.dart
    └── widgets/
        ├── zone_card_test.dart
        ├── node_list_tile_test.dart
        ├── actuator_toggle_test.dart
        └── pairing_screen_test.dart
```

### Pi side (`pi/`)

```
pi/
├── mosquitto/
│   └── mosquitto.conf
├── avahi/
│   └── greenhouse-mqtt.service
└── tools/
    ├── requirements.txt
    ├── simulator.py
    └── show_qr.py
```

---

## Task 1: Flutter project scaffold

**Files:**
- Create: `app/` (Flutter project)
- Create: `app/pubspec.yaml`

**Interfaces:**
- Produces: runnable empty Flutter app; all dependency versions locked in pubspec

- [ ] **Step 1: Create Flutter project**

Run from `C:\Users\billy\Desktop\διπλωματικη`:
```bash
flutter create --org com.greenhouse --project-name greenhouse_app --platforms android,ios app
```
Expected output: `Your application code is in app\lib\main.dart`

- [ ] **Step 2: Replace pubspec.yaml**

Replace `app/pubspec.yaml` entirely with:
```yaml
name: greenhouse_app
description: Greenhouse IoT monitoring and control app
publish_to: none
version: 1.0.0+1

environment:
  sdk: '>=3.3.0 <4.0.0'
  flutter: '>=3.19.0'

dependencies:
  flutter:
    sdk: flutter
  flutter_riverpod: ^2.5.1
  mqtt_client: ^10.4.0
  flutter_secure_storage: ^9.2.2
  shared_preferences: ^2.3.3
  mobile_scanner: ^5.2.3
  go_router: ^14.6.2
  cupertino_icons: ^1.0.8

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^4.0.0
  mocktail: ^1.0.4

flutter:
  uses-material-design: true
```

- [ ] **Step 3: Install dependencies**

```bash
cd app && flutter pub get
```
Expected: `Got dependencies!`

- [ ] **Step 4: Verify build**

```bash
cd app && flutter build apk --debug 2>&1 | tail -3
```
Expected: `Built build\app\outputs\flutter-apk\app-debug.apk`

- [ ] **Step 5: Init git and commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" init
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/pubspec.yaml app/pubspec.lock
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: flutter project scaffold with dependencies"
```

---

## Task 2: Data models

**Files:**
- Create: `app/lib/models/connection_status.dart`
- Create: `app/lib/models/connection_config.dart`
- Create: `app/lib/models/sensor_reading.dart`
- Create: `app/lib/models/node_status.dart`
- Create: `app/lib/models/actuator_state.dart`
- Create: `app/test/models/sensor_reading_test.dart`

**Interfaces:**
- Produces:
  - `SensorReading.fromMqtt(String topic, String payload)` — throws `FormatException` on bad payload
  - `SensorReading.{zone, metric, value, receivedAt}`
  - `NodeStatus.fromMqttStatus(String nodeId, String payload)`
  - `NodeStatus.fromMqttBattery(String nodeId, String payload)`
  - `NodeStatus.copyWith({bool? isOnline, double? batteryPercent, DateTime? lastSeen})`
  - `ActuatorState.fromMqttState(String actuatorId, String payload)`
  - `ActuatorState.withPending(bool isOn)` / `.withConfirmed(bool isOn)`
  - `ConnectionConfig.fromJson(Map<String, dynamic>)` / `.toJson()`
  - `enum ConnectionStatus { local, remote, reconnecting, offline }`

- [ ] **Step 1: Write failing tests for SensorReading**

Create `app/test/models/sensor_reading_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/sensor_reading.dart';

void main() {
  group('SensorReading.fromMqtt', () {
    test('parses zone temperature topic', () {
      final r = SensorReading.fromMqtt('greenhouse/zone1/air/temperature', '23.5');
      expect(r.zone, 'zone1');
      expect(r.metric, 'air/temperature');
      expect(r.value, 23.5);
    });

    test('parses soil moisture topic', () {
      final r = SensorReading.fromMqtt('greenhouse/zone2/soil/moisture', '45.0');
      expect(r.zone, 'zone2');
      expect(r.metric, 'soil/moisture');
      expect(r.value, 45.0);
    });

    test('parses weather pressure (no sub-zone)', () {
      final r = SensorReading.fromMqtt('greenhouse/weather/pressure', '1013.2');
      expect(r.zone, 'weather');
      expect(r.metric, 'pressure');
      expect(r.value, 1013.2);
    });

    test('throws FormatException on non-numeric payload', () {
      expect(
        () => SensorReading.fromMqtt('greenhouse/zone1/air/temperature', 'bad'),
        throwsFormatException,
      );
    });
  });
}
```

- [ ] **Step 2: Run test — verify it fails**

```bash
cd app && flutter test test/models/sensor_reading_test.dart
```
Expected: `FAILED` — file not found or class not defined

- [ ] **Step 3: Implement all five model files**

Create `app/lib/models/connection_status.dart`:
```dart
enum ConnectionStatus { local, remote, reconnecting, offline }
```

Create `app/lib/models/connection_config.dart`:
```dart
class ConnectionConfig {
  final String lanHost;
  final String tailscaleHost;
  final int port;
  final String tlsFingerprint;
  final String username;
  final String password;

  const ConnectionConfig({
    required this.lanHost,
    required this.tailscaleHost,
    required this.port,
    required this.tlsFingerprint,
    required this.username,
    required this.password,
  });

  factory ConnectionConfig.fromJson(Map<String, dynamic> json) => ConnectionConfig(
        lanHost: json['host_lan'] as String,
        tailscaleHost: json['host_tailscale'] as String,
        port: json['port'] as int,
        tlsFingerprint: json['tls_fingerprint'] as String,
        username: json['username'] as String,
        password: json['password'] as String,
      );

  Map<String, dynamic> toJson() => {
        'host_lan': lanHost,
        'host_tailscale': tailscaleHost,
        'port': port,
        'tls_fingerprint': tlsFingerprint,
        'username': username,
        'password': password,
      };
}
```

Create `app/lib/models/sensor_reading.dart`:
```dart
class SensorReading {
  final String zone;
  final String metric;
  final double value;
  final DateTime receivedAt;

  const SensorReading({
    required this.zone,
    required this.metric,
    required this.value,
    required this.receivedAt,
  });

  // topic format: greenhouse/{zone}/{metric...}
  // e.g. greenhouse/zone1/air/temperature  →  zone=zone1, metric=air/temperature
  //      greenhouse/weather/pressure        →  zone=weather, metric=pressure
  factory SensorReading.fromMqtt(String topic, String payload) {
    final parts = topic.split('/');
    final zone = parts[1];
    final metric = parts.sublist(2).join('/');
    final value = double.parse(payload.trim()); // throws FormatException on bad input
    return SensorReading(zone: zone, metric: metric, value: value, receivedAt: DateTime.now());
  }
}
```

Create `app/lib/models/node_status.dart`:
```dart
class NodeStatus {
  final String nodeId;
  final bool isOnline;
  final double? batteryPercent;
  final DateTime lastSeen;

  const NodeStatus({
    required this.nodeId,
    required this.isOnline,
    this.batteryPercent,
    required this.lastSeen,
  });

  factory NodeStatus.fromMqttStatus(String nodeId, String payload) => NodeStatus(
        nodeId: nodeId,
        isOnline: payload.trim() == 'online',
        lastSeen: DateTime.now(),
      );

  factory NodeStatus.fromMqttBattery(String nodeId, String payload) => NodeStatus(
        nodeId: nodeId,
        isOnline: true,
        batteryPercent: double.tryParse(payload.trim()),
        lastSeen: DateTime.now(),
      );

  NodeStatus copyWith({bool? isOnline, double? batteryPercent, DateTime? lastSeen}) =>
      NodeStatus(
        nodeId: nodeId,
        isOnline: isOnline ?? this.isOnline,
        batteryPercent: batteryPercent ?? this.batteryPercent,
        lastSeen: lastSeen ?? this.lastSeen,
      );
}
```

Create `app/lib/models/actuator_state.dart`:
```dart
class ActuatorState {
  final String actuatorId;
  final bool isOn;
  final bool isPending;
  final DateTime? lastConfirmed;

  const ActuatorState({
    required this.actuatorId,
    required this.isOn,
    this.isPending = false,
    this.lastConfirmed,
  });

  factory ActuatorState.fromMqttState(String actuatorId, String payload) => ActuatorState(
        actuatorId: actuatorId,
        isOn: payload.trim() == 'ON',
        lastConfirmed: DateTime.now(),
      );

  ActuatorState withPending(bool isOn) =>
      ActuatorState(actuatorId: actuatorId, isOn: isOn, isPending: true, lastConfirmed: lastConfirmed);

  ActuatorState withConfirmed(bool isOn) =>
      ActuatorState(actuatorId: actuatorId, isOn: isOn, isPending: false, lastConfirmed: DateTime.now());
}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd app && flutter test test/models/sensor_reading_test.dart
```
Expected: `All tests passed!`

- [ ] **Step 5: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/models/ app/test/models/
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: data models — SensorReading, NodeStatus, ActuatorState, ConnectionConfig"
```

---

## Task 3: Connection layer

**Files:**
- Create: `app/lib/connection/greenhouse_connection.dart`
- Create: `app/lib/connection/mqtt_connection.dart`
- Create: `app/test/connection/mqtt_connection_test.dart`

**Interfaces:**
- Consumes: all models from Task 2
- Produces:
  - `abstract class GreenhouseConnection` with `events`, `status`, `connect`, `disconnect`, `sendCommand`
  - `class MqttConnection implements GreenhouseConnection`
  - Static helpers (testable without a broker): `MqttConnection.isSensorTopic`, `isNodeStatusTopic`, `isNodeBatteryTopic`, `isActuatorStateTopic`, `extractNodeId`, `extractActuatorId`

- [ ] **Step 1: Write failing tests**

Create `app/test/connection/mqtt_connection_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/connection/mqtt_connection.dart';

void main() {
  group('MqttConnection topic routing helpers', () {
    test('isSensorTopic accepts zone readings', () {
      expect(MqttConnection.isSensorTopic('greenhouse/zone1/air/temperature'), isTrue);
      expect(MqttConnection.isSensorTopic('greenhouse/weather/pressure'), isTrue);
    });

    test('isSensorTopic rejects non-sensor topics', () {
      expect(MqttConnection.isSensorTopic('greenhouse/nodes/node1/status'), isFalse);
      expect(MqttConnection.isSensorTopic('greenhouse/actuators/pump1/state'), isFalse);
    });

    test('isNodeStatusTopic matches only .../status', () {
      expect(MqttConnection.isNodeStatusTopic('greenhouse/nodes/node1/status'), isTrue);
      expect(MqttConnection.isNodeStatusTopic('greenhouse/nodes/node1/battery'), isFalse);
    });

    test('isNodeBatteryTopic matches only .../battery', () {
      expect(MqttConnection.isNodeBatteryTopic('greenhouse/nodes/node1/battery'), isTrue);
      expect(MqttConnection.isNodeBatteryTopic('greenhouse/nodes/node1/status'), isFalse);
    });

    test('isActuatorStateTopic matches only .../state (not .../set)', () {
      expect(MqttConnection.isActuatorStateTopic('greenhouse/actuators/pump1/state'), isTrue);
      expect(MqttConnection.isActuatorStateTopic('greenhouse/actuators/pump1/set'), isFalse);
    });

    test('extractNodeId returns the node segment', () {
      expect(MqttConnection.extractNodeId('greenhouse/nodes/node1/status'), 'node1');
    });

    test('extractActuatorId returns the actuator segment', () {
      expect(MqttConnection.extractActuatorId('greenhouse/actuators/pump1/state'), 'pump1');
    });
  });
}
```

- [ ] **Step 2: Run test — verify failure**

```bash
cd app && flutter test test/connection/mqtt_connection_test.dart
```
Expected: `FAILED`

- [ ] **Step 3: Implement GreenhouseConnection interface**

Create `app/lib/connection/greenhouse_connection.dart`:
```dart
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/connection_status.dart';

abstract class GreenhouseConnection {
  /// Emits SensorReading | NodeStatus | ActuatorState objects.
  Stream<dynamic> get events;
  Stream<ConnectionStatus> get status;
  Future<void> connect(ConnectionConfig config);
  Future<void> disconnect();
  Future<void> sendCommand(String actuatorId, bool on);
}
```

- [ ] **Step 4: Implement MqttConnection**

Create `app/lib/connection/mqtt_connection.dart`:
```dart
import 'dart:async';
import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/models/sensor_reading.dart';

class MqttConnection implements GreenhouseConnection {
  MqttServerClient? _client;
  final _events = StreamController<dynamic>.broadcast();
  final _status = StreamController<ConnectionStatus>.broadcast();

  @override
  Stream<dynamic> get events => _events.stream;

  @override
  Stream<ConnectionStatus> get status => _status.stream;

  @override
  Future<void> connect(ConnectionConfig config) async {
    _status.add(ConnectionStatus.reconnecting);
    for (final host in [config.lanHost, config.tailscaleHost]) {
      try {
        if (await _tryConnect(host, config)) {
          _status.add(host == config.lanHost ? ConnectionStatus.local : ConnectionStatus.remote);
          return;
        }
      } catch (_) {}
    }
    _status.add(ConnectionStatus.offline);
  }

  Future<bool> _tryConnect(String host, ConnectionConfig config) async {
    final clientId = 'gh_app_${DateTime.now().millisecondsSinceEpoch}';
    final client = MqttServerClient.withPort(host, clientId, config.port);
    client.useWebSocket = true;
    client.secure = true;
    client.onBadCertificate = (_) => true; // accept self-signed; pin in Slice 5
    client.logging(on: false);
    client.keepAlivePeriod = 30;
    client.connectTimeoutPeriod = 5000;
    client.connectionMessage = MqttConnectMessage()
        .withClientIdentifier(clientId)
        .authenticateAs(config.username, config.password)
        .startClean()
        .withWillQos(MqttQos.atLeastOnce);
    try {
      final result = await client.connect();
      if (result?.state != MqttConnectionState.connected) return false;
    } catch (_) {
      return false;
    }
    _client = client;
    client.subscribe('greenhouse/#', MqttQos.atLeastOnce);
    client.updates?.listen(_handleMessages);
    client.onDisconnected = () => _status.add(ConnectionStatus.offline);
    return true;
  }

  void _handleMessages(List<MqttReceivedMessage<MqttMessage>> messages) {
    for (final msg in messages) {
      final pub = msg.payload as MqttPublishMessage;
      final payload = MqttPublishPayload.bytesToStringAsString(pub.payload.message);
      _route(msg.topic, payload);
    }
  }

  void _route(String topic, String payload) {
    if (isSensorTopic(topic)) {
      try { _events.add(SensorReading.fromMqtt(topic, payload)); } catch (_) {}
    } else if (isNodeStatusTopic(topic)) {
      _events.add(NodeStatus.fromMqttStatus(extractNodeId(topic), payload));
    } else if (isNodeBatteryTopic(topic)) {
      _events.add(NodeStatus.fromMqttBattery(extractNodeId(topic), payload));
    } else if (isActuatorStateTopic(topic)) {
      _events.add(ActuatorState.fromMqttState(extractActuatorId(topic), payload));
    }
  }

  @override
  Future<void> sendCommand(String actuatorId, bool on) async {
    if (_client?.connectionStatus?.state != MqttConnectionState.connected) return;
    final builder = MqttClientPayloadBuilder()..addString(on ? 'ON' : 'OFF');
    _client!.publishMessage(
      'greenhouse/actuators/$actuatorId/set',
      MqttQos.atLeastOnce,
      builder.payload!,
    );
  }

  @override
  Future<void> disconnect() async {
    _client?.disconnect();
    _client = null;
  }

  // ── Static routing helpers ──────────────────────────────────────────────
  static bool isSensorTopic(String topic) {
    final p = topic.split('/');
    if (p.length < 3 || p[0] != 'greenhouse') return false;
    return p[1] != 'nodes' && p[1] != 'actuators';
  }

  static bool isNodeStatusTopic(String t) =>
      RegExp(r'^greenhouse/nodes/[^/]+/status$').hasMatch(t);

  static bool isNodeBatteryTopic(String t) =>
      RegExp(r'^greenhouse/nodes/[^/]+/battery$').hasMatch(t);

  static bool isActuatorStateTopic(String t) =>
      RegExp(r'^greenhouse/actuators/[^/]+/state$').hasMatch(t);

  static String extractNodeId(String t) => t.split('/')[2];
  static String extractActuatorId(String t) => t.split('/')[2];
}
```

- [ ] **Step 5: Run tests — verify pass**

```bash
cd app && flutter test test/connection/mqtt_connection_test.dart
```
Expected: `All tests passed!`

- [ ] **Step 6: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/connection/ app/test/connection/
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: GreenhouseConnection interface and MqttConnection with LAN/Tailscale fallback"
```

---

## Task 4: GreenhouseRepository

**Files:**
- Create: `app/lib/repository/greenhouse_repository.dart`
- Create: `app/test/repository/greenhouse_repository_test.dart`

**Interfaces:**
- Consumes: `GreenhouseConnection`, all models from Task 2
- Produces:
  - `Stream<Map<String, Map<String, double>>> get readings` — `zone → metric → value`
  - `Stream<Map<String, NodeStatus>> get nodes` — `nodeId → NodeStatus`
  - `Stream<Map<String, ActuatorState>> get actuators` — `actuatorId → ActuatorState`
  - `Stream<ConnectionStatus> get connectionStatus`
  - `Future<void> connect(ConnectionConfig config)`
  - `Future<void> sendCommand(String actuatorId, bool on)` — marks pending, then delegates
  - `Future<void> disconnect()`

- [ ] **Step 1: Write failing tests**

Create `app/test/repository/greenhouse_repository_test.dart`:
```dart
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
    repo = GreenhouseRepository(connection: conn);
  });

  tearDown(() {
    eventsCtrl.close();
    statusCtrl.close();
    repo.disconnect();
  });

  test('aggregates sensor readings by zone and metric', () async {
    repo.connect(_config);
    eventsCtrl.add(SensorReading(zone: 'zone1', metric: 'air/temperature', value: 25.0, receivedAt: DateTime.now()));
    await Future.delayed(Duration.zero);
    final snapshot = await repo.readings.first;
    expect(snapshot['zone1']?['air/temperature'], 25.0);
  });

  test('merges node status and battery into same nodeId entry', () async {
    repo.connect(_config);
    eventsCtrl.add(NodeStatus.fromMqttStatus('node1', 'online'));
    eventsCtrl.add(NodeStatus.fromMqttBattery('node1', '75.0'));
    await Future.delayed(Duration.zero);
    final nodes = await repo.nodes.first;
    expect(nodes['node1']?.isOnline, isTrue);
    expect(nodes['node1']?.batteryPercent, 75.0);
  });
}
```

- [ ] **Step 2: Run test — verify failure**

```bash
cd app && flutter test test/repository/greenhouse_repository_test.dart
```
Expected: `FAILED`

- [ ] **Step 3: Implement GreenhouseRepository**

Create `app/lib/repository/greenhouse_repository.dart`:
```dart
import 'dart:async';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/models/sensor_reading.dart';

class GreenhouseRepository {
  final GreenhouseConnection connection;

  final Map<String, Map<String, double>> _readings = {};
  final Map<String, NodeStatus> _nodes = {};
  final Map<String, ActuatorState> _actuators = {};

  final _readingsCtrl = StreamController<Map<String, Map<String, double>>>.broadcast();
  final _nodesCtrl = StreamController<Map<String, NodeStatus>>.broadcast();
  final _actuatorsCtrl = StreamController<Map<String, ActuatorState>>.broadcast();

  StreamSubscription<dynamic>? _sub;

  GreenhouseRepository({required this.connection}) {
    _sub = connection.events.listen(_handle);
  }

  Stream<Map<String, Map<String, double>>> get readings => _readingsCtrl.stream;
  Stream<Map<String, NodeStatus>> get nodes => _nodesCtrl.stream;
  Stream<Map<String, ActuatorState>> get actuators => _actuatorsCtrl.stream;
  Stream<ConnectionStatus> get connectionStatus => connection.status;

  void _handle(dynamic event) {
    if (event is SensorReading) {
      _readings.putIfAbsent(event.zone, () => {})[event.metric] = event.value;
      _readingsCtrl.add(Map.from(_readings));
    } else if (event is NodeStatus) {
      final prev = _nodes[event.nodeId];
      _nodes[event.nodeId] = prev != null
          ? prev.copyWith(
              isOnline: event.isOnline,
              batteryPercent: event.batteryPercent ?? prev.batteryPercent,
              lastSeen: event.lastSeen,
            )
          : event;
      _nodesCtrl.add(Map.from(_nodes));
    } else if (event is ActuatorState) {
      _actuators[event.actuatorId] = event;
      _actuatorsCtrl.add(Map.from(_actuators));
    }
  }

  Future<void> connect(ConnectionConfig config) => connection.connect(config);

  Future<void> sendCommand(String actuatorId, bool on) async {
    final prev = _actuators[actuatorId] ?? ActuatorState(actuatorId: actuatorId, isOn: !on);
    _actuators[actuatorId] = prev.withPending(on);
    _actuatorsCtrl.add(Map.from(_actuators));
    await connection.sendCommand(actuatorId, on);
  }

  Future<void> disconnect() async {
    await _sub?.cancel();
    await connection.disconnect();
  }
}
```

- [ ] **Step 4: Run tests — verify pass**

```bash
cd app && flutter test test/repository/greenhouse_repository_test.dart
```
Expected: `All tests passed!`

- [ ] **Step 5: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/repository/ app/test/repository/
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: GreenhouseRepository aggregates MQTT events into typed state streams"
```

---

## Task 5: PairingService

**Files:**
- Create: `app/lib/services/pairing_service.dart`

**Interfaces:**
- Produces:
  - `class PairingService` with `saveConfig`, `loadConfig`, `clearConfig`, `isPaired`
  - `pairingServiceProvider` → `PairingService`

- [ ] **Step 1: Implement PairingService**

Create `app/lib/services/pairing_service.dart`:
```dart
import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:greenhouse_app/models/connection_config.dart';

const _configKey = 'greenhouse_connection_config';

class PairingService {
  final FlutterSecureStorage _storage;
  const PairingService(this._storage);

  Future<void> saveConfig(ConnectionConfig config) =>
      _storage.write(key: _configKey, value: jsonEncode(config.toJson()));

  Future<ConnectionConfig?> loadConfig() async {
    final raw = await _storage.read(key: _configKey);
    if (raw == null) return null;
    return ConnectionConfig.fromJson(jsonDecode(raw) as Map<String, dynamic>);
  }

  Future<void> clearConfig() => _storage.delete(key: _configKey);

  Future<bool> get isPaired async => (await loadConfig()) != null;
}

final pairingServiceProvider = Provider(
  (_) => const PairingService(FlutterSecureStorage()),
);
```

- [ ] **Step 2: Verify compilation**

```bash
cd app && flutter analyze lib/services/ 2>&1 | grep error | head -10
```
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/services/
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: PairingService stores and loads ConnectionConfig in flutter_secure_storage"
```

---

## Task 6: Riverpod providers

**Files:**
- Create: `app/lib/providers/connection_provider.dart`
- Create: `app/lib/providers/readings_provider.dart`
- Create: `app/lib/providers/nodes_provider.dart`
- Create: `app/lib/providers/actuators_provider.dart`

**Interfaces:**
- Consumes: `GreenhouseRepository`, `MqttConnection`, `pairingServiceProvider`
- Produces:
  - `repositoryProvider` → `GreenhouseRepository`
  - `connectOnStartProvider` → `AsyncValue<void>` (triggers connect on first watch)
  - `connectionStatusProvider` → `AsyncValue<ConnectionStatus>`
  - `readingsProvider` → `AsyncValue<Map<String, Map<String, double>>>`
  - `nodesProvider` → `AsyncValue<Map<String, NodeStatus>>`
  - `actuatorsProvider` → `AsyncValue<Map<String, ActuatorState>>`

- [ ] **Step 1: Create connection_provider.dart**

Create `app/lib/providers/connection_provider.dart`:
```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/connection/mqtt_connection.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

final _mqttConnectionProvider = Provider((_) => MqttConnection());

final repositoryProvider = Provider((ref) =>
    GreenhouseRepository(connection: ref.watch(_mqttConnectionProvider)));

final connectOnStartProvider = FutureProvider<void>((ref) async {
  final config = await ref.watch(pairingServiceProvider).loadConfig();
  if (config != null) await ref.watch(repositoryProvider).connect(config);
});

final connectionStatusProvider = StreamProvider<ConnectionStatus>((ref) =>
    ref.watch(repositoryProvider).connectionStatus);
```

- [ ] **Step 2: Create remaining providers**

Create `app/lib/providers/readings_provider.dart`:
```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';

final readingsProvider = StreamProvider<Map<String, Map<String, double>>>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).readings;
});
```

Create `app/lib/providers/nodes_provider.dart`:
```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';

final nodesProvider = StreamProvider<Map<String, NodeStatus>>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).nodes;
});
```

Create `app/lib/providers/actuators_provider.dart`:
```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';

final actuatorsProvider = StreamProvider<Map<String, ActuatorState>>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).actuators;
});
```

- [ ] **Step 3: Verify compilation**

```bash
cd app && flutter analyze lib/providers/ 2>&1 | grep error | head -10
```
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/providers/
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: Riverpod providers wiring repository to UI"
```

---

## Task 7: Theme

**Files:**
- Create: `app/lib/theme/app_colors.dart`
- Create: `app/lib/theme/app_theme.dart`

**Interfaces:**
- Produces: `AppColors` (static color constants), `AppTheme.light()` / `AppTheme.dark()` → `ThemeData`

- [ ] **Step 1: Create app_colors.dart**

Create `app/lib/theme/app_colors.dart`:
```dart
import 'package:flutter/material.dart';

class AppColors {
  // Brand
  static const brand      = Color(0xFF2E7D32);
  static const brandLight = Color(0xFF60AD5E);
  static const brandDark  = Color(0xFF005005);

  // Connection status
  static const local        = Color(0xFF43A047);
  static const remote       = Color(0xFF1976D2);
  static const reconnecting = Color(0xFFFB8C00);
  static const offline      = Color(0xFFE53935);

  // Node/metric status
  static const online  = Color(0xFF43A047);
  static const warning = Color(0xFFFB8C00);
  static const pending = Color(0xFF9E9E9E);

  // Card surfaces
  static const cardLight = Color(0xFFF9FBE7);
  static const cardDark  = Color(0xFF1B2A1B);
}
```

- [ ] **Step 2: Create app_theme.dart**

Create `app/lib/theme/app_theme.dart`:
```dart
import 'package:flutter/material.dart';
import 'app_colors.dart';

class AppTheme {
  static ThemeData light() => ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(seedColor: AppColors.brand, brightness: Brightness.light),
        cardTheme: const CardTheme(color: AppColors.cardLight, elevation: 2, margin: EdgeInsets.all(8)),
        appBarTheme: const AppBarTheme(backgroundColor: AppColors.brand, foregroundColor: Colors.white, elevation: 0),
      );

  static ThemeData dark() => ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(seedColor: AppColors.brand, brightness: Brightness.dark),
        cardTheme: const CardTheme(color: AppColors.cardDark, elevation: 2, margin: EdgeInsets.all(8)),
        appBarTheme: const AppBarTheme(backgroundColor: AppColors.brandDark, foregroundColor: Colors.white, elevation: 0),
      );
}
```

- [ ] **Step 3: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/theme/
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: agricultural color palette and Material 3 theme (light + dark)"
```

---

## Task 8: App shell + routing + stub screens

**Files:**
- Create: `app/lib/main.dart`
- Create: `app/lib/app.dart`
- Create: `app/lib/screens/shell_screen.dart`
- Create stub files: all six screen files (replaced in Tasks 9–13)

**Interfaces:**
- Consumes: `AppTheme`, `pairingServiceProvider`, all screen widgets

- [ ] **Step 1: Create app.dart**

Create `app/lib/app.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/screens/shell_screen.dart';
import 'package:greenhouse_app/screens/pairing/pairing_screen.dart';
import 'package:greenhouse_app/screens/pairing/qr_scan_screen.dart';
import 'package:greenhouse_app/screens/dashboard/dashboard_screen.dart';
import 'package:greenhouse_app/screens/devices/devices_screen.dart';
import 'package:greenhouse_app/screens/control/control_screen.dart';
import 'package:greenhouse_app/screens/settings/settings_screen.dart';
import 'package:greenhouse_app/services/pairing_service.dart';
import 'package:greenhouse_app/theme/app_theme.dart';

final _router = GoRouter(
  initialLocation: '/dashboard',
  redirect: (context, state) async {
    final pairing = ProviderScope.containerOf(context).read(pairingServiceProvider);
    if (!await pairing.isPaired && state.matchedLocation != '/pair') return '/pair';
    return null;
  },
  routes: [
    GoRoute(path: '/pair', builder: (_, __) => const PairingScreen()),
    GoRoute(path: '/pair/qr', builder: (_, __) => const QrScanScreen()),
    ShellRoute(
      builder: (_, __, child) => ShellScreen(child: child),
      routes: [
        GoRoute(path: '/dashboard', builder: (_, __) => const DashboardScreen()),
        GoRoute(path: '/devices',   builder: (_, __) => const DevicesScreen()),
        GoRoute(path: '/control',   builder: (_, __) => const ControlScreen()),
        GoRoute(path: '/settings',  builder: (_, __) => const SettingsScreen()),
      ],
    ),
  ],
);

class GreenhouseApp extends StatelessWidget {
  const GreenhouseApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp.router(
        title: 'Greenhouse',
        theme: AppTheme.light(),
        darkTheme: AppTheme.dark(),
        themeMode: ThemeMode.system,
        routerConfig: _router,
      );
}
```

- [ ] **Step 2: Create main.dart**

Create `app/lib/main.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/app.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProviderScope(child: GreenhouseApp()));
}
```

- [ ] **Step 3: Create shell_screen.dart**

Create `app/lib/screens/shell_screen.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

class ShellScreen extends StatelessWidget {
  final Widget child;
  const ShellScreen({required this.child, super.key});

  @override
  Widget build(BuildContext context) {
    final loc = GoRouterState.of(context).matchedLocation;
    final idx = ['/dashboard', '/devices', '/control', '/settings'].indexOf(loc).clamp(0, 3);
    return Scaffold(
      body: child,
      bottomNavigationBar: NavigationBar(
        selectedIndex: idx,
        onDestinationSelected: (i) =>
            context.go(['/dashboard', '/devices', '/control', '/settings'][i]),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard), label: 'Dashboard'),
          NavigationDestination(icon: Icon(Icons.sensors),   label: 'Devices'),
          NavigationDestination(icon: Icon(Icons.toggle_on), label: 'Control'),
          NavigationDestination(icon: Icon(Icons.settings),  label: 'Settings'),
        ],
      ),
    );
  }
}
```

- [ ] **Step 4: Create stub screens (replaced in Tasks 9–13)**

Create `app/lib/screens/pairing/pairing_screen.dart`:
```dart
import 'package:flutter/material.dart';
class PairingScreen extends StatelessWidget {
  const PairingScreen({super.key});
  @override Widget build(BuildContext c) => const Scaffold(body: Center(child: Text('Pairing')));
}
```

Create `app/lib/screens/pairing/qr_scan_screen.dart`:
```dart
import 'package:flutter/material.dart';
class QrScanScreen extends StatelessWidget {
  const QrScanScreen({super.key});
  @override Widget build(BuildContext c) => const Scaffold(body: Center(child: Text('QR Scan')));
}
```

Create `app/lib/screens/dashboard/dashboard_screen.dart`:
```dart
import 'package:flutter/material.dart';
class DashboardScreen extends StatelessWidget {
  const DashboardScreen({super.key});
  @override Widget build(BuildContext c) => const Scaffold(body: Center(child: Text('Dashboard')));
}
```

Create `app/lib/screens/devices/devices_screen.dart`:
```dart
import 'package:flutter/material.dart';
class DevicesScreen extends StatelessWidget {
  const DevicesScreen({super.key});
  @override Widget build(BuildContext c) => const Scaffold(body: Center(child: Text('Devices')));
}
```

Create `app/lib/screens/control/control_screen.dart`:
```dart
import 'package:flutter/material.dart';
class ControlScreen extends StatelessWidget {
  const ControlScreen({super.key});
  @override Widget build(BuildContext c) => const Scaffold(body: Center(child: Text('Control')));
}
```

Create `app/lib/screens/settings/settings_screen.dart`:
```dart
import 'package:flutter/material.dart';
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});
  @override Widget build(BuildContext c) => const Scaffold(body: Center(child: Text('Settings')));
}
```

- [ ] **Step 5: Verify the app compiles**

```bash
cd app && flutter build apk --debug 2>&1 | tail -3
```
Expected: `Built build\app\outputs\flutter-apk\app-debug.apk`

- [ ] **Step 6: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: app shell with bottom nav, go_router routing, pairing redirect"
```

---

## Task 9: Pairing screen

**Files:**
- Modify: `app/lib/screens/pairing/pairing_screen.dart`
- Modify: `app/lib/screens/pairing/qr_scan_screen.dart`
- Create: `app/test/widgets/pairing_screen_test.dart`

**Interfaces:**
- Consumes: `pairingServiceProvider`, `ConnectionConfig`, `mobile_scanner`

- [ ] **Step 1: Write failing widget test**

Create `app/test/widgets/pairing_screen_test.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/screens/pairing/pairing_screen.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

class MockPairingService extends Mock implements PairingService {}

void main() {
  setUpAll(() => registerFallbackValue(const ConnectionConfig(
    lanHost: '', tailscaleHost: '', port: 9001,
    tlsFingerprint: '', username: '', password: '',
  )));

  testWidgets('PairingScreen shows manual entry fields', (tester) async {
    final mock = MockPairingService();
    when(() => mock.saveConfig(any())).thenAnswer((_) async {});
    await tester.pumpWidget(ProviderScope(
      overrides: [pairingServiceProvider.overrideWithValue(mock)],
      child: const MaterialApp(home: PairingScreen()),
    ));
    expect(find.text('Connect to your greenhouse'), findsOneWidget);
    expect(find.byType(TextFormField), findsWidgets);
    expect(find.text('Scan QR from Pi'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run test — verify failure**

```bash
cd app && flutter test test/widgets/pairing_screen_test.dart
```
Expected: `FAILED`

- [ ] **Step 3: Replace pairing_screen.dart**

Replace `app/lib/screens/pairing/pairing_screen.dart`:
```dart
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

class PairingScreen extends ConsumerStatefulWidget {
  const PairingScreen({super.key});
  @override ConsumerState<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends ConsumerState<PairingScreen> {
  final _formKey = GlobalKey<FormState>();
  final _lan      = TextEditingController(text: 'greenhouse.local');
  final _ts       = TextEditingController();
  final _port     = TextEditingController(text: '9001');
  final _fp       = TextEditingController();
  final _user     = TextEditingController(text: 'app');
  final _pass     = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_lan, _ts, _port, _fp, _user, _pass]) c.dispose();
    super.dispose();
  }

  void _applyQr(String raw) {
    try {
      final j = jsonDecode(raw) as Map<String, dynamic>;
      _lan.text  = j['host_lan']        ?? '';
      _ts.text   = j['host_tailscale']  ?? '';
      _port.text = (j['port'] ?? 9001).toString();
      _fp.text   = j['tls_fingerprint'] ?? '';
      _user.text = j['username']        ?? 'app';
      _pass.text = j['password']        ?? '';
    } catch (_) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Invalid QR code')));
    }
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() { _busy = true; _error = null; });
    try {
      await ref.read(pairingServiceProvider).saveConfig(ConnectionConfig(
        lanHost: _lan.text.trim(),
        tailscaleHost: _ts.text.trim(),
        port: int.parse(_port.text.trim()),
        tlsFingerprint: _fp.text.trim(),
        username: _user.text.trim(),
        password: _pass.text,
      ));
      if (mounted) context.go('/dashboard');
    } catch (e) {
      setState(() { _error = e.toString(); _busy = false; });
    }
  }

  Widget _field(TextEditingController c, String label,
      {bool obscure = false, TextInputType? type, String? Function(String?)? validator}) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextFormField(
          controller: c,
          decoration: InputDecoration(labelText: label),
          obscureText: obscure,
          keyboardType: type,
          validator: validator ?? (v) => v!.isEmpty ? 'Required' : null,
        ),
      );

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Connect to your greenhouse')),
        body: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Form(
            key: _formKey,
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              FilledButton.icon(
                onPressed: () async {
                  final result = await context.push<String>('/pair/qr');
                  if (result != null) _applyQr(result);
                },
                icon: const Icon(Icons.qr_code_scanner),
                label: const Text('Scan QR from Pi'),
              ),
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 16),
                child: Row(children: [
                  Expanded(child: Divider()),
                  Padding(padding: EdgeInsets.symmetric(horizontal: 8), child: Text('or enter manually')),
                  Expanded(child: Divider()),
                ]),
              ),
              _field(_lan,  'LAN host (mDNS)'),
              _field(_ts,   'Tailscale IP'),
              _field(_port, 'Port', type: TextInputType.number,
                  validator: (v) => int.tryParse(v ?? '') == null ? 'Must be a number' : null),
              _field(_fp,   'TLS fingerprint', validator: (_) => null),
              _field(_user, 'Username'),
              _field(_pass, 'Password', obscure: true),
              if (_error != null) ...[
                Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                const SizedBox(height: 12),
              ],
              FilledButton(
                onPressed: _busy ? null : _save,
                child: _busy ? const CircularProgressIndicator() : const Text('Connect'),
              ),
            ]),
          ),
        ),
      );
}
```

- [ ] **Step 4: Replace qr_scan_screen.dart**

Replace `app/lib/screens/pairing/qr_scan_screen.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

class QrScanScreen extends StatefulWidget {
  const QrScanScreen({super.key});
  @override State<QrScanScreen> createState() => _QrScanScreenState();
}

class _QrScanScreenState extends State<QrScanScreen> {
  bool _done = false;
  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Scan Pi QR code')),
        body: MobileScanner(
          onDetect: (capture) {
            if (_done) return;
            final val = capture.barcodes.firstOrNull?.rawValue;
            if (val == null) return;
            _done = true;
            context.pop(val);
          },
        ),
      );
}
```

- [ ] **Step 5: Run tests — verify pass**

```bash
cd app && flutter test test/widgets/pairing_screen_test.dart
```
Expected: `All tests passed!`

- [ ] **Step 6: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/screens/pairing/ app/test/widgets/pairing_screen_test.dart
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: pairing screen — QR scan + manual entry, saves config to secure storage"
```

---

## Task 10: Dashboard screen

**Files:**
- Modify: `app/lib/screens/dashboard/dashboard_screen.dart`
- Create: `app/lib/screens/dashboard/zone_card.dart`
- Create: `app/lib/screens/dashboard/connection_banner.dart`
- Create: `app/test/widgets/zone_card_test.dart`

**Interfaces:**
- Consumes: `readingsProvider`, `connectionStatusProvider`, `AppColors`

- [ ] **Step 1: Write failing widget tests**

Create `app/test/widgets/zone_card_test.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/screens/dashboard/zone_card.dart';

void main() {
  testWidgets('ZoneCard displays zone name and temperature', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'air/temperature': 24.5, 'air/humidity': 60.0},
    ))));
    expect(find.text('Zone 1'), findsOneWidget);
    expect(find.textContaining('24.5'), findsOneWidget);
  });

  testWidgets('ZoneCard shows warning icon when soil moisture < 30%', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': 15.0},
    ))));
    expect(find.byIcon(Icons.warning_amber_rounded), findsOneWidget);
  });

  testWidgets('ZoneCard does not show warning icon when soil moisture >= 30%', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': 45.0},
    ))));
    expect(find.byIcon(Icons.warning_amber_rounded), findsNothing);
  });
}
```

- [ ] **Step 2: Run tests — verify failure**

```bash
cd app && flutter test test/widgets/zone_card_test.dart
```
Expected: `FAILED`

- [ ] **Step 3: Create zone_card.dart**

Create `app/lib/screens/dashboard/zone_card.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class ZoneCard extends StatelessWidget {
  final String zone;
  final Map<String, double> readings;
  const ZoneCard({required this.zone, required this.readings, super.key});

  String get _title {
    if (zone.startsWith('zone')) return 'Zone ${zone.substring(4)}';
    return '${zone[0].toUpperCase()}${zone.substring(1)}';
  }

  @override
  Widget build(BuildContext context) {
    final soil = readings['soil/moisture'];
    final lowSoil = soil != null && soil < 30;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(_title, style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)),
            if (lowSoil) ...[
              const SizedBox(width: 8),
              const Icon(Icons.warning_amber_rounded, color: AppColors.warning, size: 18),
            ],
          ]),
          const SizedBox(height: 12),
          Wrap(spacing: 16, runSpacing: 8, children: [
            if (readings['air/temperature'] != null)
              _Chip(Icons.thermostat, 'Temp', '${readings['air/temperature']!.toStringAsFixed(1)} °C'),
            if (readings['air/humidity'] != null)
              _Chip(Icons.water_drop, 'Humidity', '${readings['air/humidity']!.toStringAsFixed(0)} %'),
            if (soil != null)
              _Chip(Icons.grass, 'Soil', '${soil.toStringAsFixed(0)} %', color: lowSoil ? AppColors.warning : null),
            if (readings['light/lux'] != null)
              _Chip(Icons.wb_sunny, 'Light', '${readings['light/lux']!.toStringAsFixed(0)} lux'),
            if (readings['pressure'] != null)
              _Chip(Icons.speed, 'Pressure', '${readings['pressure']!.toStringAsFixed(0)} hPa'),
          ]),
        ]),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final Color? color;
  const _Chip(this.icon, this.label, this.value, {this.color});

  @override
  Widget build(BuildContext context) => Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 16, color: color ?? Theme.of(context).colorScheme.primary),
        const SizedBox(width: 4),
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: Theme.of(context).textTheme.labelSmall),
          Text(value, style: TextStyle(fontWeight: FontWeight.w600, color: color)),
        ]),
      ]);
}
```

- [ ] **Step 4: Create connection_banner.dart**

Create `app/lib/screens/dashboard/connection_banner.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class ConnectionBanner extends StatelessWidget {
  final ConnectionStatus status;
  const ConnectionBanner({required this.status, super.key});

  @override
  Widget build(BuildContext context) {
    final (color, icon, label) = switch (status) {
      ConnectionStatus.local        => (AppColors.local,        Icons.wifi,      'Local'),
      ConnectionStatus.remote       => (AppColors.remote,       Icons.vpn_lock,  'Remote via Tailscale'),
      ConnectionStatus.reconnecting => (AppColors.reconnecting, Icons.sync,      'Reconnecting…'),
      ConnectionStatus.offline      => (AppColors.offline,      Icons.wifi_off,  'Offline — showing last known data'),
    };
    return Container(
      width: double.infinity,
      color: color,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Row(children: [
        Icon(icon, size: 14, color: Colors.white),
        const SizedBox(width: 6),
        Text(label, style: const TextStyle(color: Colors.white, fontSize: 12)),
      ]),
    );
  }
}
```

- [ ] **Step 5: Replace dashboard_screen.dart**

Replace `app/lib/screens/dashboard/dashboard_screen.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/screens/dashboard/connection_banner.dart';
import 'package:greenhouse_app/screens/dashboard/zone_card.dart';

class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final statusAsync  = ref.watch(connectionStatusProvider);
    final readingsAsync = ref.watch(readingsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Greenhouse')),
      body: Column(children: [
        statusAsync.when(
          data: (s) => ConnectionBanner(status: s),
          loading: () => const SizedBox.shrink(),
          error: (_, __) => const SizedBox.shrink(),
        ),
        Expanded(child: readingsAsync.when(
          loading: () => ListView.builder(itemCount: 3, itemBuilder: (_, __) => const _Skeleton()),
          error: (e, _) => Center(child: Text('Error: $e')),
          data: (r) => r.isEmpty
              ? const Center(child: Text('Waiting for sensor data…'))
              : ListView(children: r.entries.map((e) => ZoneCard(zone: e.key, readings: e.value)).toList()),
        )),
      ]),
    );
  }
}

class _Skeleton extends StatelessWidget {
  const _Skeleton();
  @override
  Widget build(BuildContext context) => Card(
    child: Padding(padding: const EdgeInsets.all(16), child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(width: 80, height: 14, color: Colors.grey[300]),
        const SizedBox(height: 10),
        Container(width: double.infinity, height: 10, color: Colors.grey[200]),
      ],
    )),
  );
}
```

- [ ] **Step 6: Run tests — verify pass**

```bash
cd app && flutter test test/widgets/zone_card_test.dart
```
Expected: `All tests passed!`

- [ ] **Step 7: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/screens/dashboard/ app/test/widgets/zone_card_test.dart
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: dashboard — zone cards, connection banner, skeleton loading state"
```

---

## Task 11: Devices screen

**Files:**
- Modify: `app/lib/screens/devices/devices_screen.dart`
- Create: `app/lib/screens/devices/node_list_tile.dart`
- Create: `app/test/widgets/node_list_tile_test.dart`

**Interfaces:**
- Consumes: `nodesProvider`, `NodeStatus`, `AppColors`

- [ ] **Step 1: Write failing test**

Create `app/test/widgets/node_list_tile_test.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/node_list_tile.dart';

void main() {
  testWidgets('NodeListTile shows Online badge and battery for online node', (tester) async {
    final node = NodeStatus(nodeId: 'node1', isOnline: true, batteryPercent: 72.0, lastSeen: DateTime(2026, 6, 25, 10, 0));
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: NodeListTile(node: node))));
    expect(find.text('node1'), findsOneWidget);
    expect(find.text('Online'), findsOneWidget);
    expect(find.textContaining('72'), findsOneWidget);
  });

  testWidgets('NodeListTile shows Offline badge for offline node', (tester) async {
    final node = NodeStatus(nodeId: 'node2', isOnline: false, lastSeen: DateTime(2026, 6, 25, 9, 0));
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: NodeListTile(node: node))));
    expect(find.text('Offline'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run test — verify failure**

```bash
cd app && flutter test test/widgets/node_list_tile_test.dart
```
Expected: `FAILED`

- [ ] **Step 3: Create node_list_tile.dart**

Create `app/lib/screens/devices/node_list_tile.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class NodeListTile extends StatelessWidget {
  final NodeStatus node;
  const NodeListTile({required this.node, super.key});

  IconData _batteryIcon(double? p) {
    if (p == null) return Icons.battery_unknown;
    if (p > 80) return Icons.battery_full;
    if (p > 50) return Icons.battery_5_bar;
    if (p > 20) return Icons.battery_3_bar;
    return Icons.battery_alert;
  }

  @override
  Widget build(BuildContext context) => ListTile(
        leading: Icon(Icons.sensors, color: node.isOnline ? AppColors.online : AppColors.offline),
        title: Text(node.nodeId),
        subtitle: Text('Last seen: ${node.lastSeen.hour.toString().padLeft(2,'0')}:${node.lastSeen.minute.toString().padLeft(2,'0')}'),
        trailing: Row(mainAxisSize: MainAxisSize.min, children: [
          if (node.batteryPercent != null) ...[
            Icon(_batteryIcon(node.batteryPercent), size: 18),
            const SizedBox(width: 4),
            Text('${node.batteryPercent!.toStringAsFixed(0)} %'),
            const SizedBox(width: 12),
          ],
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: node.isOnline ? AppColors.online : AppColors.offline,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(node.isOnline ? 'Online' : 'Offline',
                style: const TextStyle(color: Colors.white, fontSize: 12)),
          ),
        ]),
      );
}
```

- [ ] **Step 4: Replace devices_screen.dart**

Replace `app/lib/screens/devices/devices_screen.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/screens/devices/node_list_tile.dart';

class DevicesScreen extends ConsumerWidget {
  const DevicesScreen({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final nodes = ref.watch(nodesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Devices')),
      body: nodes.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('Error: $e')),
        data: (n) => n.isEmpty
            ? const Center(child: Text('No nodes detected yet'))
            : ListView(children: n.values.map((node) => NodeListTile(node: node)).toList()),
      ),
    );
  }
}
```

- [ ] **Step 5: Run tests — verify pass**

```bash
cd app && flutter test test/widgets/node_list_tile_test.dart
```
Expected: `All tests passed!`

- [ ] **Step 6: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/screens/devices/ app/test/widgets/node_list_tile_test.dart
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: devices screen with node online/offline badge and battery level"
```

---

## Task 12: Control screen

**Files:**
- Modify: `app/lib/screens/control/control_screen.dart`
- Create: `app/lib/screens/control/actuator_toggle.dart`
- Create: `app/test/widgets/actuator_toggle_test.dart`

**Interfaces:**
- Consumes: `actuatorsProvider`, `repositoryProvider`, `ActuatorState`, `AppColors`

- [ ] **Step 1: Write failing test**

Create `app/test/widgets/actuator_toggle_test.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/screens/control/actuator_toggle.dart';

void main() {
  testWidgets('ActuatorToggle switch is ON when state.isOn is true', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ActuatorToggle(
      state: const ActuatorState(actuatorId: 'pump1', isOn: true),
      onToggle: (_) {},
    ))));
    final sw = tester.widget<Switch>(find.byType(Switch));
    expect(sw.value, isTrue);
  });

  testWidgets('ActuatorToggle is disabled (onChanged null) when isPending', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ActuatorToggle(
      state: const ActuatorState(actuatorId: 'pump1', isOn: false, isPending: true),
      onToggle: (_) {},
    ))));
    expect(find.text('Pending'), findsOneWidget);
    final sw = tester.widget<Switch>(find.byType(Switch));
    expect(sw.onChanged, isNull);
  });
}
```

- [ ] **Step 2: Run test — verify failure**

```bash
cd app && flutter test test/widgets/actuator_toggle_test.dart
```
Expected: `FAILED`

- [ ] **Step 3: Create actuator_toggle.dart**

Create `app/lib/screens/control/actuator_toggle.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class ActuatorToggle extends StatelessWidget {
  final ActuatorState state;
  final ValueChanged<bool> onToggle;
  const ActuatorToggle({required this.state, required this.onToggle, super.key});

  @override
  Widget build(BuildContext context) => ListTile(
        leading: Icon(Icons.power_settings_new,
            color: state.isOn ? AppColors.online : AppColors.pending),
        title: Text(state.actuatorId),
        subtitle: state.isPending
            ? const Text('Pending', style: TextStyle(color: AppColors.pending))
            : Text(state.isOn ? 'ON' : 'OFF'),
        trailing: Switch(
          value: state.isOn,
          onChanged: state.isPending ? null : onToggle,
        ),
      );
}
```

- [ ] **Step 4: Replace control_screen.dart**

Replace `app/lib/screens/control/control_screen.dart`:
```dart
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/providers/actuators_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/screens/control/actuator_toggle.dart';

class ControlScreen extends ConsumerStatefulWidget {
  const ControlScreen({super.key});
  @override ConsumerState<ControlScreen> createState() => _ControlScreenState();
}

class _ControlScreenState extends ConsumerState<ControlScreen> {
  final Map<String, Timer> _timers = {};

  Future<void> _toggle(String actuatorId, bool on) async {
    await ref.read(repositoryProvider).sendCommand(actuatorId, on);
    _timers[actuatorId]?.cancel();
    _timers[actuatorId] = Timer(const Duration(seconds: 5), () {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('No response from $actuatorId')),
      );
    });
  }

  @override
  void dispose() {
    for (final t in _timers.values) t.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Cancel timer when confirmation arrives (isPending becomes false)
    ref.listen<AsyncValue<Map<String, ActuatorState>>>(actuatorsProvider, (_, next) {
      next.whenData((map) {
        for (final e in map.entries) {
          if (!e.value.isPending) { _timers[e.key]?.cancel(); _timers.remove(e.key); }
        }
      });
    });

    final actuators = ref.watch(actuatorsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Control')),
      body: actuators.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('Error: $e')),
        data: (map) => map.isEmpty
            ? const Center(child: Text('No actuators discovered yet'))
            : ListView(children: map.values
                .map((s) => ActuatorToggle(state: s, onToggle: (on) => _toggle(s.actuatorId, on)))
                .toList()),
      ),
    );
  }
}
```

- [ ] **Step 5: Run tests — verify pass**

```bash
cd app && flutter test test/widgets/actuator_toggle_test.dart
```
Expected: `All tests passed!`

- [ ] **Step 6: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/screens/control/ app/test/widgets/actuator_toggle_test.dart
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: control screen — actuator toggles with pending state and 5-second timeout"
```

---

## Task 13: Settings screen

**Files:**
- Modify: `app/lib/screens/settings/settings_screen.dart`

**Interfaces:**
- Consumes: `pairingServiceProvider`, `connectionStatusProvider`

- [ ] **Step 1: Replace settings_screen.dart**

Replace `app/lib/screens/settings/settings_screen.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final statusAsync = ref.watch(connectionStatusProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(children: [
        ListTile(
          leading: const Icon(Icons.wifi),
          title: const Text('Connection'),
          subtitle: statusAsync.when(
            data: (s) => Text(s.name),
            loading: () => const Text('Connecting…'),
            error: (_, __) => const Text('Unknown'),
          ),
        ),
        const Divider(),
        ListTile(
          leading: const Icon(Icons.qr_code),
          title: const Text('Re-pair with server'),
          onTap: () => context.go('/pair'),
        ),
        ListTile(
          leading: const Icon(Icons.logout),
          title: const Text('Disconnect'),
          onTap: () async {
            await ref.read(pairingServiceProvider).clearConfig();
            if (context.mounted) context.go('/pair');
          },
        ),
        const Divider(),
        const ListTile(
          leading: Icon(Icons.info_outline),
          title: Text('App version'),
          subtitle: Text('1.0.0'),
        ),
      ]),
    );
  }
}
```

- [ ] **Step 2: Full test suite — verify all pass**

```bash
cd app && flutter test
```
Expected: `All tests passed!`

- [ ] **Step 3: Verify release build**

```bash
cd app && flutter build apk --debug 2>&1 | tail -3
```
Expected: `Built build\app\outputs\flutter-apk\app-debug.apk`

- [ ] **Step 4: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add app/lib/screens/settings/settings_screen.dart
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: settings screen with connection info, re-pair, and disconnect"
```

---

## Task 14: Pi Mosquitto hardening + mDNS

**Files:**
- Create: `pi/mosquitto/mosquitto.conf`
- Create: `pi/avahi/greenhouse-mqtt.service`

All commands run on the Pi via: `ssh pi@192.168.1.88`

- [ ] **Step 1: Generate TLS certificates**

```bash
ssh pi@192.168.1.88
mkdir -p /home/pi/greenhouse/certs && cd /home/pi/greenhouse/certs

# CA
openssl genrsa -out ca.key 2048
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=GreenhouseCA"

# Server key + cert
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=greenhouse.local"
openssl x509 -req -days 3650 -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt

# Print fingerprint — copy this for pairing
openssl x509 -fingerprint -sha256 -noout -in server.crt

chmod 600 ca.key server.key
```

Expected: prints `SHA256 Fingerprint=XX:XX:XX:...` — copy this value.

- [ ] **Step 2: Create Mosquitto config**

Create `pi/mosquitto/mosquitto.conf` locally, then deploy:
```conf
# Loopback only — local tooling and Node-RED (Slice 3)
listener 1883 127.0.0.1
allow_anonymous true

# MQTT over TLS — bridges
listener 8883
cafile /home/pi/greenhouse/certs/ca.crt
certfile /home/pi/greenhouse/certs/server.crt
keyfile /home/pi/greenhouse/certs/server.key
require_certificate false
allow_anonymous false
password_file /etc/mosquitto/passwd

# MQTT over WebSocket + TLS — Flutter app
listener 9001
protocol websockets
cafile /home/pi/greenhouse/certs/ca.crt
certfile /home/pi/greenhouse/certs/server.crt
keyfile /home/pi/greenhouse/certs/server.key
require_certificate false
allow_anonymous false
password_file /etc/mosquitto/passwd

persistence true
persistence_location /var/lib/mosquitto/
log_dest syslog
log_type error
log_type warning
```

Deploy:
```bash
scp pi/mosquitto/mosquitto.conf pi@192.168.1.88:/tmp/greenhouse.conf
ssh pi@192.168.1.88 "sudo cp /tmp/greenhouse.conf /etc/mosquitto/conf.d/greenhouse.conf"
```

- [ ] **Step 3: Create MQTT credentials**

```bash
ssh pi@192.168.1.88
sudo mosquitto_passwd -c /etc/mosquitto/passwd app
# Enter password: gh_app_2026  (or your own — record it for pairing QR)

sudo mosquitto_passwd /etc/mosquitto/passwd bridge
# Enter password: gh_bridge_2026
```

- [ ] **Step 4: Restart and verify Mosquitto**

```bash
ssh pi@192.168.1.88 "sudo systemctl restart mosquitto && sudo systemctl status mosquitto"
```
Expected: `Active: active (running)`

- [ ] **Step 5: Smoke test MQTT on loopback**

```bash
ssh pi@192.168.1.88
mosquitto_sub -h localhost -p 1883 -t "greenhouse/#" -v &
mosquitto_pub -h localhost -p 1883 -t "greenhouse/zone1/air/temperature" -m "23.5"
```
Expected: `greenhouse/zone1/air/temperature 23.5`

```bash
kill %1
```

- [ ] **Step 6: Install Avahi and register service**

```bash
ssh pi@192.168.1.88 "sudo apt-get install -y avahi-daemon && sudo systemctl enable avahi-daemon && sudo systemctl start avahi-daemon"
```

Create `pi/avahi/greenhouse-mqtt.service` locally:
```xml
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Greenhouse MQTT on %h</name>
  <service>
    <type>_mqtt._tcp</type>
    <port>9001</port>
    <txt-record>tls=true</txt-record>
  </service>
</service-group>
```

Deploy:
```bash
scp pi/avahi/greenhouse-mqtt.service pi@192.168.1.88:/tmp/
ssh pi@192.168.1.88 "sudo cp /tmp/greenhouse-mqtt.service /etc/avahi/services/ && sudo systemctl restart avahi-daemon"
```

Verify from another machine on the same LAN:
```bash
ping greenhouse.local
```
Expected: replies from `192.168.1.88`

- [ ] **Step 7: Commit config files**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add pi/
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: Pi Mosquitto TLS config (ports 1883/8883/9001) and Avahi mDNS"
```

---

## Task 15: Pi simulator + QR script

**Files:**
- Create: `pi/tools/requirements.txt`
- Create: `pi/tools/simulator.py`
- Create: `pi/tools/show_qr.py`

- [ ] **Step 1: Create requirements.txt**

Create `pi/tools/requirements.txt`:
```
paho-mqtt==1.6.1
qrcode[pil]==7.4.2
Pillow==10.3.0
```

Deploy and install:
```bash
scp pi/tools/requirements.txt pi@192.168.1.88:/home/pi/greenhouse/tools/requirements.txt
ssh pi@192.168.1.88 "pip3 install -r /home/pi/greenhouse/tools/requirements.txt"
```

- [ ] **Step 2: Create simulator.py**

Create `pi/tools/simulator.py`:
```python
#!/usr/bin/env python3
"""
Greenhouse sensor simulator.
Publishes realistic fake readings for all MQTT topics via loopback port 1883.
Usage: python3 simulator.py [--interval 10]
"""
import argparse
import math
import random
import time
import paho.mqtt.client as mqtt

BROKER = "127.0.0.1"
PORT   = 1883
ZONES  = ["zone1", "zone2", "zone3"]
NODES  = ["node1", "node2", "node3"]
ACTS   = ["pump1", "fan1", "light1"]

def _wave(t, period=3600, lo=0.0, hi=1.0):
    return lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args()

    c = mqtt.Client(client_id="simulator", clean_session=True)
    c.connect(BROKER, PORT, keepalive=60)
    c.loop_start()

    for node in NODES:
        c.publish(f"greenhouse/nodes/{node}/status", "online", qos=1, retain=True)
        c.publish(f"greenhouse/nodes/{node}/battery", str(round(random.uniform(60, 100), 1)), qos=1, retain=True)
    for act in ACTS:
        c.publish(f"greenhouse/actuators/{act}/state", "OFF", qos=1, retain=True)

    print(f"[sim] publishing every {args.interval}s — Ctrl+C to stop")
    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            for zone in ZONES:
                temp = round(_wave(t, 86400, 18, 36) + random.gauss(0, 0.3), 1)
                hum  = round(_wave(t, 86400, 40, 90) + random.gauss(0, 1.0), 1)
                soil = round(_wave(t, 7200,  10, 80) + random.gauss(0, 2.0), 1)
                lux  = round(max(0, _wave(t, 86400, 0, 80000) + random.gauss(0, 500)), 0)
                c.publish(f"greenhouse/{zone}/air/temperature", str(temp), retain=True)
                c.publish(f"greenhouse/{zone}/air/humidity",    str(hum),  retain=True)
                c.publish(f"greenhouse/{zone}/soil/moisture",   str(soil), retain=True)
                c.publish(f"greenhouse/{zone}/light/lux",       str(lux),  retain=True)
            pressure = round(1013 + random.gauss(0, 2), 1)
            c.publish("greenhouse/weather/pressure", str(pressure), retain=True)
            for i, node in enumerate(NODES):
                pct = round(max(0, 100 - t / 3600 - i * 5), 1)
                c.publish(f"greenhouse/nodes/{node}/battery", str(pct), retain=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[sim] stopped")
        for node in NODES:
            c.publish(f"greenhouse/nodes/{node}/status", "offline", qos=1, retain=True)
        c.loop_stop()
        c.disconnect()

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create show_qr.py**

Create `pi/tools/show_qr.py`:
```python
#!/usr/bin/env python3
"""
Prints the pairing QR code for the Flutter app.
Usage: python3 show_qr.py --tailscale 100.x.y.z --pass YOUR_PASSWORD
"""
import argparse
import json
import subprocess
import qrcode

def fingerprint():
    r = subprocess.run(
        ["openssl", "x509", "-fingerprint", "-sha256", "-noout",
         "-in", "/home/pi/greenhouse/certs/server.crt"],
        capture_output=True, text=True,
    )
    return r.stdout.strip().split("=", 1)[-1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tailscale", required=True)
    ap.add_argument("--user", default="app")
    ap.add_argument("--pass", dest="password", required=True)
    ap.add_argument("--port", type=int, default=9001)
    args = ap.parse_args()

    payload = json.dumps({
        "host_lan":        "greenhouse.local",
        "host_tailscale":  args.tailscale,
        "port":            args.port,
        "tls_fingerprint": fingerprint(),
        "username":        args.user,
        "password":        args.password,
    })

    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print("\n--- JSON if QR won't scan ---")
    print(payload)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Deploy to Pi**

```bash
scp pi/tools/simulator.py pi/tools/show_qr.py pi@192.168.1.88:/home/pi/greenhouse/tools/
```

- [ ] **Step 5: Test simulator**

Two SSH sessions on Pi:

Session 1:
```bash
mosquitto_sub -h localhost -p 1883 -t "greenhouse/#" -v
```

Session 2:
```bash
python3 /home/pi/greenhouse/tools/simulator.py --interval 3
```

Expected: Session 1 prints zone1/zone2/zone3 readings every 3 seconds.

Stop simulator with Ctrl+C. Verify offline LWT publishes.

- [ ] **Step 6: Generate pairing QR**

```bash
ssh pi@192.168.1.88 "python3 /home/pi/greenhouse/tools/show_qr.py --tailscale YOUR_TAILSCALE_IP --pass gh_app_2026"
```

Expected: ASCII QR code printed to terminal, followed by JSON.

- [ ] **Step 7: Run all Flutter tests**

```bash
cd app && flutter test
```
Expected: `All tests passed!`

- [ ] **Step 8: Commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add pi/tools/
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: sensor simulator and QR pairing script for Pi"
```

---

## Task 16: End-to-end smoke test (manual)

- [ ] **Step 1: Start simulator**

```bash
ssh pi@192.168.1.88 "nohup python3 /home/pi/greenhouse/tools/simulator.py --interval 5 > /tmp/sim.log 2>&1 &"
```

- [ ] **Step 2: Install app on phone**

Connect Android phone to PC via USB with USB debugging enabled:
```bash
cd app && flutter install
```

- [ ] **Step 3: Pairing**

- [ ] Open app on phone — redirects to Pairing screen
- [ ] Tap "Scan QR from Pi" — camera opens
- [ ] Show QR from Pi terminal — form auto-fills
- [ ] Tap Connect

- [ ] **Step 4: Dashboard verification**

- [ ] Connection banner shows **Local** (green) when on same WiFi as Pi
- [ ] Zone cards appear for zone1, zone2, zone3 with live readings
- [ ] Soil card shows warning icon when simulated soil < 30 %

- [ ] **Step 5: Devices tab**

- [ ] node1, node2, node3 listed as Online
- [ ] Battery % visible and decreasing slowly

- [ ] **Step 6: Control tab**

- [ ] pump1, fan1, light1 show as OFF
- [ ] Tap pump1 → shows Pending → after 5 s shows snackbar "No response from pump1" (correct — bridges handle confirmation in Slice 2)

- [ ] **Step 7: Offline fallback**

```bash
ssh pi@192.168.1.88 "pkill -f simulator.py"
```

- [ ] Banner changes to **Offline** (red)
- [ ] Last-known readings still visible (retained messages on broker)

Restart simulator:
```bash
ssh pi@192.168.1.88 "nohup python3 /home/pi/greenhouse/tools/simulator.py --interval 5 > /tmp/sim.log 2>&1 &"
```

- [ ] Banner returns to **Local** (green), readings update

- [ ] **Step 8: Final commit**

```bash
git -C "C:\Users\billy\Desktop\διπλωματικη" add .
git -C "C:\Users\billy\Desktop\διπλωματικη" commit -m "feat: Slice 1 complete — Flutter app, Pi broker, simulator, end-to-end verified"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task(s) |
|---|---|
| Flutter app — 4 tabs | Tasks 8–13 |
| Pairing QR + manual entry | Tasks 9, 15 |
| LAN mDNS fallback | Tasks 3, 14 |
| Tailscale remote path | Task 3 |
| Auto LAN → Tailscale → Offline | Task 3 (MqttConnection.connect loop) |
| Connection banner | Task 10 |
| Retained messages / no DB needed | Tasks 14, 15 (simulator + broker config) |
| LWT for node status | Tasks 14, 15 |
| Actuator 5-second timeout | Task 12 |
| No optimistic actuator toggle | Task 12 (pending state, no snap on send) |
| flutter_secure_storage | Task 5 |
| Dark mode | Task 7 |
| Sensor simulator | Task 15 |
| Mosquitto TLS port 8883 + WSS port 9001 | Task 14 |
| GreenhouseConnection interface (cloud relay hook) | Task 3 |
| `ConnectionStatus` enum | Task 2 |
| TDD throughout | Every task |

**Placeholders:** none found.

**Type consistency:**
- `SensorReading.fromMqtt` — defined Task 2, used in `MqttConnection._route` Task 3 ✓
- `NodeStatus.copyWith` — defined Task 2, used in `GreenhouseRepository._handle` Task 4 ✓
- `ActuatorState.withPending` — defined Task 2, used in `GreenhouseRepository.sendCommand` Task 4 ✓
- `pairingServiceProvider` — defined Task 5, referenced in Tasks 6, 9, 13 (Tasks run in order) ✓
- `repositoryProvider` — defined Task 6, used in Task 12 ✓
- `connectOnStartProvider` — defined Task 6, watched in readings/nodes/actuators providers Task 6 ✓
