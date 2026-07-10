import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/connection/mqtt_connection.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/models/weather_alert.dart';
import 'package:greenhouse_app/models/weather_rule.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';
import 'package:greenhouse_app/services/fcm_token_service.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

final mqttConnectionProvider = Provider((_) => MqttConnection());

final repositoryProvider = Provider((ref) =>
    GreenhouseRepository(connection: ref.watch(mqttConnectionProvider)));

final fcmTokenServiceProvider = Provider(
    (ref) => FcmTokenService(ref.watch(repositoryProvider)));

final connectOnStartProvider = FutureProvider<void>((ref) async {
  debugPrint('[CONNECT] provider running');
  final config = await ref.read(pairingServiceProvider).loadConfig();
  debugPrint('[CONNECT] config=$config');
  if (config != null) {
    debugPrint('[CONNECT] calling connect lan=${config.lanHost} port=${config.port}');
    await ref.read(repositoryProvider).connect(config);
  }
});

final connectionStatusProvider = StreamProvider<ConnectionStatus>((ref) =>
    ref.watch(repositoryProvider).connectionStatus);

/// Emits each WeatherAlert as it arrives via MQTT.
final weatherAlertsProvider = StreamProvider<WeatherAlert>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).alerts;
});

/// Emits the current list of automation rules from the Pi.
final rulesProvider = StreamProvider<List<WeatherRule>>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).rules;
});

/// Emits 24-hour forecast data from greenhouse/weather/forecast.
final forecastProvider = StreamProvider<Map<String, dynamic>>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).forecast;
});

