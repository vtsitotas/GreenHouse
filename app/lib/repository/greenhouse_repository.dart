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
