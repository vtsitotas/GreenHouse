// PARKED — camera MQTT topic-routing tests.
//
// These covered MqttConnection's isCamStatusTopic / isCamEventResponseTopic /
// extractCamEventReqId / isCamLiveFrameTopic helpers, removed from
// app/lib/connection/mqtt_connection.dart when the camera was parked. Kept
// verbatim, not compiled, not run by CI — fold back into
// app/test/connection/mqtt_connection_test.dart's "topic helpers" group when
// restoring. See parked/camera/README.md.

import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/connection/mqtt_connection.dart';

void main() {
  group('MqttConnection camera topic helpers', () {
    test('isCamStatusTopic matches only the status topic', () {
      expect(MqttConnection.isCamStatusTopic('greenhouse/cam/status'), isTrue);
      expect(MqttConnection.isCamStatusTopic('greenhouse/cam/live/frame'), isFalse);
    });

    test('isCamEventResponseTopic matches response/<id> and extracts the id', () {
      expect(MqttConnection.isCamEventResponseTopic('greenhouse/cam/event/response/req1'), isTrue);
      expect(MqttConnection.isCamEventResponseTopic('greenhouse/cam/event/request'), isFalse);
      expect(MqttConnection.extractCamEventReqId('greenhouse/cam/event/response/req1'), 'req1');
    });

    test('isCamLiveFrameTopic matches only the live frame topic', () {
      expect(MqttConnection.isCamLiveFrameTopic('greenhouse/cam/live/frame'), isTrue);
      expect(MqttConnection.isCamLiveFrameTopic('greenhouse/cam/live/start'), isFalse);
    });
  });
}
