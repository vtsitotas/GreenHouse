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
import 'package:greenhouse_app/models/weather_alert.dart';
import 'package:greenhouse_app/models/weather_events.dart';

class MqttConnection implements GreenhouseConnection {
  MqttServerClient? _client;
  final _events = StreamController<dynamic>.broadcast();
  final _status = StreamController<ConnectionStatus>.broadcast();
  int _generation = 0;

  @override
  Stream<dynamic> get events => _events.stream;

  @override
  Stream<ConnectionStatus> get status => _status.stream;

  @override
  Future<void> connect(ConnectionConfig config) async {
    _generation++;
    final gen = _generation;
    _status.add(ConnectionStatus.reconnecting);
    if (await _attempt(config, gen)) return;
    _scheduleRetry(config, gen);
  }

  Future<bool> _attempt(ConnectionConfig config, int gen) async {
    final hosts = [
      (config.lanHost,    config.username,       config.password),
      (config.remoteHost, config.remoteUsername,  config.remotePassword),
    ];
    for (final (host, user, pass) in hosts) {
      try {
        if (await _tryConnect(host, user, pass, config, gen)) {
          _status.add(host == config.lanHost ? ConnectionStatus.local : ConnectionStatus.remote);
          return true;
        }
      } catch (_) {}
    }
    _status.add(ConnectionStatus.offline);
    return false;
  }

  Future<void> _scheduleRetry(ConnectionConfig config, int gen) async {
    int delay = 10;
    while (_generation == gen) {
      await Future.delayed(Duration(seconds: delay));
      if (_generation != gen) return;
      _status.add(ConnectionStatus.reconnecting);
      if (await _attempt(config, gen)) return;
      delay = (delay * 2).clamp(10, 60);
    }
  }

  Future<bool> _tryConnect(String host, String user, String pass,
      ConnectionConfig config, int gen) async {
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
        .authenticateAs(user, pass)
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
    client.onDisconnected = () {
      _status.add(ConnectionStatus.offline);
      if (_generation == gen) _scheduleRetry(config, gen);
    };
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
    if (isWeatherAlertTopic(topic)) {
      try { _events.add(WeatherAlert.fromMqtt(payload)); } catch (_) {}
    } else if (isWeatherForecastTopic(topic)) {
      _events.add(WeatherForecastRaw(payload));
    } else if (isRulesCurrentTopic(topic)) {
      _events.add(RulesPayloadRaw(payload));
    } else if (isNotificationSettingsTopic(topic)) {
      _events.add(NotificationSettingsRaw(payload));
    } else if (isHistoryResponseTopic(topic)) {
      _events.add(HistoryResponseRaw(topic.substring(_historyResponsePrefix.length), payload));
    } else if (isSensorTopic(topic)) {
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
    _generation++;
    _client?.disconnect();
    _client = null;
  }

  @override
  Future<void> publishRaw(String topic, String payload, {bool retain = false}) async {
    if (_client?.connectionStatus?.state != MqttConnectionState.connected) return;
    final builder = MqttClientPayloadBuilder()..addString(payload);
    _client!.publishMessage(
      topic,
      MqttQos.atLeastOnce,
      builder.payload!,
      retain: retain,
    );
  }

  Future<bool> testConnect(ConnectionConfig config) async {
    final hosts = [
      (config.lanHost,    config.username,      config.password),
      (config.remoteHost, config.remoteUsername, config.remotePassword),
    ];
    for (final (host, user, pass) in hosts) {
      if (host.isEmpty) continue;
      if (await _tryConnect(host, user, pass, config, -1)) {
        _client?.onDisconnected = null;
        await disconnect();
        return true;
      }
    }
    return false;
  }

  // ── Static routing helpers ──────────────────────────────────────────────
  static bool isWeatherAlertTopic(String t) => t == 'greenhouse/weather/alert';
  static bool isWeatherForecastTopic(String t) => t == 'greenhouse/weather/forecast';
  static bool isRulesCurrentTopic(String t) => t == 'greenhouse/rules/current';
  static bool isNotificationSettingsTopic(String t) => t == 'greenhouse/settings/notifications/current';

  static const _historyResponsePrefix = 'greenhouse/history/response/';
  static bool isHistoryResponseTopic(String t) => t.startsWith(_historyResponsePrefix);

  static bool isSensorTopic(String topic) {
    final p = topic.split('/');
    if (p.length < 3 || p[0] != 'greenhouse') return false;
    // Exclude nodes, actuators, weather (non-numeric), and rules topics
    if (p[1] == 'nodes' || p[1] == 'actuators' || p[1] == 'rules') return false;
    if (p[1] == 'weather' && (p.length < 3 || p[2] == 'alert' || p[2] == 'forecast')) return false;
    return true;
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
