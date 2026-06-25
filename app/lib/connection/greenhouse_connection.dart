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
