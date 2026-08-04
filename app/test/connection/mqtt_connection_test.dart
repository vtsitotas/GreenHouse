import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/connection/mqtt_connection.dart';
import 'package:greenhouse_app/models/node_status.dart';

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

    test('isNodeMeshTopic matches only .../mesh, not lookalikes or deeper paths', () {
      expect(MqttConnection.isNodeMeshTopic('greenhouse/nodes/node1/mesh'), isTrue);
      expect(MqttConnection.isNodeMeshTopic('greenhouse/nodes/node1/meshx'), isFalse);
      expect(MqttConnection.isNodeMeshTopic('greenhouse/nodes/node1/mesh/extra'), isFalse);
      expect(MqttConnection.isNodeMeshTopic('greenhouse/nodes/node1/status'), isFalse);
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

  group('MqttConnection mesh topic routing', () {
    test('a publish on .../mesh emits a NodeStatus with parentId set and isOnline true', () async {
      final conn = MqttConnection();
      final events = <dynamic>[];
      final sub = conn.events.listen(events.add);

      conn.routeForTest(
        'greenhouse/nodes/n1/mesh',
        '{"parent":"bridge","rank":1,"rssi":-55,"sleepy":false,"battery_mv":4100,"zone":"zone1"}',
      );
      await Future(() {}); // let the broadcast controller's event reach the listener

      expect(events, hasLength(1));
      final status = events.single as NodeStatus;
      expect(status.nodeId, 'n1');
      expect(status.parentId, 'bridge');
      expect(status.isOnline, isTrue);
      expect(status.meshRank, 1);
      expect(status.parentRssi, -55);

      await sub.cancel();
    });

    test('a malformed payload on .../mesh emits nothing and does not throw', () async {
      final conn = MqttConnection();
      final events = <dynamic>[];
      final sub = conn.events.listen(events.add);

      expect(() => conn.routeForTest('greenhouse/nodes/n1/mesh', '{not json'), returnsNormally);
      await Future(() {});

      expect(events, isEmpty);

      await sub.cancel();
    });
  });
}
