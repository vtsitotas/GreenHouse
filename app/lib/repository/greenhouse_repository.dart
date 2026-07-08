import 'dart:async';
import 'dart:convert';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/models/sensor_reading.dart';
import 'package:greenhouse_app/models/weather_alert.dart';
import 'package:greenhouse_app/models/weather_events.dart';
import 'package:greenhouse_app/models/weather_rule.dart';

class GreenhouseRepository {
  final GreenhouseConnection connection;

  final Map<String, Map<String, double>> _readings   = {};
  final Map<String, NodeStatus>          _nodes      = {};
  final Map<String, ActuatorState>       _actuators  = {};

  final _readingsCtrl  = StreamController<Map<String, Map<String, double>>>.broadcast();
  final _nodesCtrl     = StreamController<Map<String, NodeStatus>>.broadcast();
  final _actuatorsCtrl = StreamController<Map<String, ActuatorState>>.broadcast();
  final _alertsCtrl    = StreamController<WeatherAlert>.broadcast();
  final _forecastCtrl  = StreamController<Map<String, dynamic>>.broadcast();
  final _rulesCtrl     = StreamController<List<WeatherRule>>.broadcast();
  final _historyRespCtrl = StreamController<HistoryResponseRaw>.broadcast();

  List<WeatherRule> _rules = [];
  Map<String, dynamic>? _lastForecast;

  StreamSubscription<dynamic>? _sub;

  GreenhouseRepository({required this.connection}) {
    _sub = connection.events.listen(_handle);
  }

  Stream<Map<String, Map<String, double>>> get readings async* {
    yield Map.from(_readings);
    yield* _readingsCtrl.stream;
  }

  Stream<Map<String, NodeStatus>> get nodes async* {
    yield Map.from(_nodes);
    yield* _nodesCtrl.stream;
  }

  Stream<Map<String, ActuatorState>> get actuators async* {
    yield Map.from(_actuators);
    yield* _actuatorsCtrl.stream;
  }

  /// Fires every time a weather alert arrives.
  Stream<WeatherAlert> get alerts => _alertsCtrl.stream;

  /// Fires when a new 24-hour forecast arrives (yields cached value immediately if available).
  Stream<Map<String, dynamic>> get forecast async* {
    if (_lastForecast != null) yield _lastForecast!;
    yield* _forecastCtrl.stream;
  }

  /// Fires when the rules list is received / updated.
  Stream<List<WeatherRule>> get rules async* {
    if (_rules.isNotEmpty) yield List.from(_rules);
    yield* _rulesCtrl.stream;
  }

  Stream<ConnectionStatus> get connectionStatus => connection.status;

  void _handle(dynamic event) {
    if (event is SensorReading) {
      _readings.putIfAbsent(event.zone, () => {})[event.metric] = event.value;
      _readingsCtrl.add(Map.from(_readings));
    } else if (event is NodeStatus) {
      final prev = _nodes[event.nodeId];
      _nodes[event.nodeId] = prev != null
          ? prev.copyWith(
              isOnline:      event.isOnline,
              batteryPercent: event.batteryPercent ?? prev.batteryPercent,
              lastSeen:      event.lastSeen,
            )
          : event;
      _nodesCtrl.add(Map.from(_nodes));
    } else if (event is ActuatorState) {
      _actuators[event.actuatorId] = event;
      _actuatorsCtrl.add(Map.from(_actuators));
    } else if (event is WeatherAlert) {
      _alertsCtrl.add(event);
    } else if (event is WeatherForecastRaw) {
      try {
        final data = jsonDecode(event.payload) as Map<String, dynamic>;
        _lastForecast = data;
        _forecastCtrl.add(data);
      } catch (_) {}
    } else if (event is RulesPayloadRaw) {
      try {
        _rules = WeatherRule.listFromJson(event.payload);
        _rulesCtrl.add(List.from(_rules));
      } catch (_) {}
    } else if (event is HistoryResponseRaw) {
      _historyRespCtrl.add(event);
    }
  }

  Future<void> connect(ConnectionConfig config) => connection.connect(config);

  Future<void> sendCommand(String actuatorId, bool on) async {
    final prev = _actuators[actuatorId] ?? ActuatorState(actuatorId: actuatorId, isOn: !on);
    _actuators[actuatorId] = prev.withPending(on);
    _actuatorsCtrl.add(Map.from(_actuators));
    await connection.sendCommand(actuatorId, on);
  }

  /// Send updated rules to the Pi (Pi saves and reloads).
  Future<void> publishRules(List<WeatherRule> rules) async {
    _rules = rules;
    _rulesCtrl.add(List.from(_rules));
    await connection.publishRaw(
      'greenhouse/rules/update',
      WeatherRule.listToJson(rules),
    );
  }

  /// Ask the Pi to broadcast its current rules.
  Future<void> requestRules() async {
    await connection.publishRaw('greenhouse/rules/get', '1');
  }

  /// Push location + interval to the Pi (retained so weather.py picks it up on restart).
  Future<void> publishLocation(double lat, double lon, {int intervalSeconds = 1800}) async {
    final payload = '{"latitude":$lat,"longitude":$lon,"timezone":"auto","interval_seconds":$intervalSeconds}';
    await connection.publishRaw('greenhouse/weather/location/set', payload, retain: true);
  }

  /// Fetches history points over MQTT (greenhouse/history/request ->
  /// greenhouse/history/response/<id>) instead of the LAN-only HTTP
  /// /api/history endpoint. Used when connected via the HiveMQ Cloud
  /// bridge, since HiveMQ only carries MQTT — not the Pi's HTTP portal.
  /// Returns null on timeout or a malformed response.
  Future<Map<String, dynamic>?> fetchHistoryViaMqtt({
    String? zone,
    String? kind,
    required String metric,
    double hours = 24,
  }) async {
    final id = 'h${DateTime.now().microsecondsSinceEpoch}';
    final payload = jsonEncode({
      'id': id,
      'type': 'points',
      if (zone != null) 'zone': zone,
      if (kind != null) 'kind': kind,
      'metric': metric,
      'hours': hours,
    });

    final completer = Completer<Map<String, dynamic>?>();
    late final StreamSubscription<HistoryResponseRaw> sub;
    sub = _historyRespCtrl.stream.listen((event) {
      if (event.id != id) return;
      sub.cancel();
      if (completer.isCompleted) return;
      try {
        completer.complete(jsonDecode(event.payload) as Map<String, dynamic>);
      } catch (_) {
        completer.complete(null);
      }
    });

    await connection.publishRaw('greenhouse/history/request', payload);

    return completer.future.timeout(const Duration(seconds: 8), onTimeout: () {
      sub.cancel();
      return null;
    });
  }

  Future<void> disconnect() async {
    await _sub?.cancel();
    await connection.disconnect();
  }
}

