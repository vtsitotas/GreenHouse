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
