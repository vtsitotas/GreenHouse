// app/test/models/sensor_enrolment_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/sensor_enrolment.dart';

void main() {
  const validKey = '000102030405060708090a0b0c0d0e0f';

  test('parses the QR payload the provisioning tool writes', () {
    final s = SensorEnrolment.fromQr(
        '{"v":1,"mac":"206EF16C9DB0","k":"$validKey"}');
    expect(s.mac, '206EF16C9DB0');
    expect(s.key, validKey);
  });

  test('uppercases and strips separators from the MAC', () {
    final s = SensorEnrolment.fromQr(
        '{"v":1,"mac":"20:6e:f1:6c:9d:b0","k":"$validKey"}');
    expect(s.mac, '206EF16C9DB0');
  });

  test('rejects a pairing QR scanned by mistake', () {
    expect(() => SensorEnrolment.fromQr('{"host":"greenhouse.local","port":8883}'),
        throwsFormatException);
  });

  test('rejects a key that is not 16 bytes', () {
    expect(() => SensorEnrolment.fromQr('{"v":1,"mac":"206EF16C9DB0","k":"aabb"}'),
        throwsFormatException);
  });

  test('rejects a future payload version rather than guessing', () {
    expect(() => SensorEnrolment.fromQr(
        '{"v":2,"mac":"206EF16C9DB0","k":"$validKey"}'), throwsFormatException);
  });

  test('rejects text that is not JSON at all', () {
    expect(() => SensorEnrolment.fromQr('hello'), throwsFormatException);
  });
}
