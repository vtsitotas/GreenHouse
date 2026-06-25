import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/connection/mqtt_connection.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

final mqttConnectionProvider = Provider((_) => MqttConnection());

final repositoryProvider = Provider((ref) =>
    GreenhouseRepository(connection: ref.watch(mqttConnectionProvider)));

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
