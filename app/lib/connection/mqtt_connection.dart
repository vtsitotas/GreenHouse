import 'dart:async';
import 'package:flutter/foundation.dart';
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
    if (host.isEmpty) return false;
    final clientId = 'gh_app_${DateTime.now().millisecondsSinceEpoch}';
    final client = MqttServerClient.withPort(host, clientId, config.port);
    client.useWebSocket = false;
    client.secure = true;
    client.onBadCertificate = (Object _) => true; // accept self-signed; pin in Slice 5
    client.logging(on: false);
    client.keepAlivePeriod = 30;
    client.connectTimeoutPeriod = 5000;
    client.connectionMessage = MqttConnectMessage()
        .withClientIdentifier(clientId)
        .authenticateAs(config.username, config.password)
        .startClean();
    try {
      debugPrint('[MQTT] trying $host:${config.port}');
      final result = await client.connect();
      debugPrint('[MQTT] result: ${result?.state}');
      if (result?.state != MqttConnectionState.connected) return false;
    } catch (e, st) {
      debugPrint('[MQTT] error on $host: $e\n$st');
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

  Future<bool> testConnect(ConnectionConfig config) async {
    for (final host in [config.lanHost, config.tailscaleHost]) {
      if (host.isEmpty) continue;
      if (await _tryConnect(host, config)) {
        await disconnect();
        return true;
      }
    }
    return false;
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
