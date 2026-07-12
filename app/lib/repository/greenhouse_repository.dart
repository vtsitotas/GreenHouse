import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/models/notification_settings.dart';
import 'package:greenhouse_app/models/sensor_reading.dart';
import 'package:greenhouse_app/models/weather_alert.dart';
import 'package:greenhouse_app/models/weather_events.dart';
import 'package:greenhouse_app/models/weather_rule.dart';
import 'package:greenhouse_app/models/cam_status.dart';

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
  final _notificationSettingsCtrl = StreamController<NotificationSettings>.broadcast();
  final _camStatusCtrl = StreamController<CamStatus>.broadcast();
  final _camEventChunkCtrl = StreamController<CamEventChunkRaw>.broadcast();
  final _camLiveFrameCtrl = StreamController<Uint8List>.broadcast();

  // Buffers chunks per in-flight live frame_id until `total` chunks have
  // arrived, then emits the reassembled bytes and drops the buffer.
  final Map<int, List<String?>> _liveFrameBuffers = {};

  List<WeatherRule> _rules = [];
  Map<String, dynamic>? _lastForecast;
  NotificationSettings? _notificationSettings;

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

  /// Fires when notification settings are received / updated.
  Stream<NotificationSettings> get notificationSettings async* {
    if (_notificationSettings != null) yield _notificationSettings!;
    yield* _notificationSettingsCtrl.stream;
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
    } else if (event is NotificationSettingsRaw) {
      try {
        final settings = NotificationSettings.fromJson(jsonDecode(event.payload) as Map<String, dynamic>);
        _notificationSettings = settings;
        _notificationSettingsCtrl.add(settings);
      } catch (_) {}
    } else if (event is CamStatusRaw) {
      try {
        _camStatusCtrl.add(CamStatus.fromJson(jsonDecode(event.payload) as Map<String, dynamic>));
      } catch (_) {}
    } else if (event is CamEventChunkRaw) {
      _camEventChunkCtrl.add(event);
    } else if (event is CamLiveFrameChunkRaw) {
      _handleLiveFrameChunk(event);
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
  /// Retained so weather.py's poller (_pull_rules_from_mqtt) reliably picks
  /// this up regardless of exact timing, the same reason publishLocation
  /// retains its message.
  Future<void> publishRules(List<WeatherRule> rules) async {
    _rules = rules;
    _rulesCtrl.add(List.from(_rules));
    await connection.publishRaw(
      'greenhouse/rules/update',
      WeatherRule.listToJson(rules),
      retain: true,
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

  /// Registers this device's current FCM token with the Pi, retained per
  /// device so weather.py (and later camera motion alerts) can look up
  /// every currently-registered device on demand.
  Future<void> registerFcmToken(String deviceId, String token) async {
    await connection.publishRaw(
        'greenhouse/app/fcm_token/$deviceId', token, retain: true);
  }

  /// Push notification-preference settings to the Pi (retained, matching
  /// publishRules/publishLocation's retained-for-reliable-polling pattern).
  Future<void> publishNotificationSettings(NotificationSettings settings) async {
    _notificationSettings = settings;
    _notificationSettingsCtrl.add(settings);
    await connection.publishRaw(
      'greenhouse/settings/notifications',
      jsonEncode(settings.toJson()),
      retain: true,
    );
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
    DateTime? since,
    DateTime? until,
  }) async {
    final isCustomRange = since != null && until != null;
    final id = 'h${DateTime.now().microsecondsSinceEpoch}';
    final payload = jsonEncode({
      'id': id,
      'type': 'points',
      if (zone != null) 'zone': zone,
      if (kind != null) 'kind': kind,
      'metric': metric,
      if (isCustomRange) 'since': since.millisecondsSinceEpoch ~/ 1000,
      if (isCustomRange) 'until': until.millisecondsSinceEpoch ~/ 1000,
      if (!isCustomRange) 'hours': hours,
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

  void _handleLiveFrameChunk(CamLiveFrameChunkRaw event) {
    try {
      final data = jsonDecode(event.payload) as Map<String, dynamic>;
      final frameId = data['frame_id'] as int;
      final chunk = data['chunk'] as int;
      final total = data['total'] as int;
      final buffer = _liveFrameBuffers.putIfAbsent(frameId, () => List<String?>.filled(total, null));
      buffer[chunk] = data['data'] as String;
      if (buffer.every((c) => c != null)) {
        final bytes = buffer.map((c) => base64Decode(c!)).expand((b) => b).toList();
        _camLiveFrameCtrl.add(Uint8List.fromList(bytes));
        _liveFrameBuffers.remove(frameId);
      }
    } catch (_) {}
  }

  /// Fires whenever the Pi publishes an updated camera status (retained, so
  /// the app gets current state immediately on connect).
  Stream<CamStatus> get camStatus => _camStatusCtrl.stream;

  /// Fires with reassembled JPEG bytes for each relayed live-view frame,
  /// only while a live session is active (see startLive/stopLive).
  Stream<Uint8List> get liveFrames => _camLiveFrameCtrl.stream;

  /// Requests a motion-event photo over MQTT (mirrors fetchHistoryViaMqtt's
  /// request/response-by-id shape) and reassembles the chunked response.
  /// Returns null on timeout or if the Pi reports the camera unreachable.
  Future<Uint8List?> fetchEventPhoto(String eventId) async {
    final id = 'e${DateTime.now().microsecondsSinceEpoch}';
    final chunks = <String?>[];
    var total = -1;

    final completer = Completer<Uint8List?>();
    late final StreamSubscription<CamEventChunkRaw> sub;
    sub = _camEventChunkCtrl.stream.listen((event) {
      if (event.reqId != id) return;
      Map<String, dynamic> data;
      try {
        data = jsonDecode(event.payload) as Map<String, dynamic>;
      } catch (_) {
        return;
      }
      if (data.containsKey('error')) {
        sub.cancel();
        if (!completer.isCompleted) completer.complete(null);
        return;
      }
      if (total == -1) {
        total = data['total'] as int;
        chunks.addAll(List<String?>.filled(total, null));
      }
      chunks[data['chunk'] as int] = data['data'] as String;
      if (chunks.every((c) => c != null)) {
        sub.cancel();
        final bytes = chunks.map((c) => base64Decode(c!)).expand((b) => b).toList();
        if (!completer.isCompleted) completer.complete(Uint8List.fromList(bytes));
      }
    });

    await connection.publishRaw('greenhouse/cam/event/request', jsonEncode({'id': id, 'event_id': eventId}));

    return completer.future.timeout(const Duration(seconds: 15), onTimeout: () {
      sub.cancel();
      return null;
    });
  }

  /// Starts (or refreshes, if already active) the on-demand remote live
  /// relay. Call again periodically (~every 30s) while the live screen stays
  /// open — cam_bridge.py auto-stops the relay after 2 minutes without one.
  Future<void> startLive() => connection.publishRaw('greenhouse/cam/live/start', '1');

  Future<void> stopLive() => connection.publishRaw('greenhouse/cam/live/stop', '1');

  Future<void> disconnect() async {
    await _sub?.cancel();
    await connection.disconnect();
  }
}

