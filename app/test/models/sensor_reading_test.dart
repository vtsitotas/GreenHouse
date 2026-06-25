import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/sensor_reading.dart';

void main() {
  group('SensorReading.fromMqtt', () {
    test('parses zone temperature topic', () {
      final r = SensorReading.fromMqtt('greenhouse/zone1/air/temperature', '23.5');
      expect(r.zone, 'zone1');
      expect(r.metric, 'air/temperature');
      expect(r.value, 23.5);
    });

    test('parses soil moisture topic', () {
      final r = SensorReading.fromMqtt('greenhouse/zone2/soil/moisture', '45.0');
      expect(r.zone, 'zone2');
      expect(r.metric, 'soil/moisture');
      expect(r.value, 45.0);
    });

    test('parses weather pressure (no sub-zone)', () {
      final r = SensorReading.fromMqtt('greenhouse/weather/pressure', '1013.2');
      expect(r.zone, 'weather');
      expect(r.metric, 'pressure');
      expect(r.value, 1013.2);
    });

    test('throws FormatException on non-numeric payload', () {
      expect(
        () => SensorReading.fromMqtt('greenhouse/zone1/air/temperature', 'bad'),
        throwsFormatException,
      );
    });
  });
}
