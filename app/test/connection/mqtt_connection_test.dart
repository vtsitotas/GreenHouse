import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/connection/mqtt_connection.dart';

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
}
