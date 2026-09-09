// app/test/services/sensor_provisioning_service_test.dart
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/sensor_enrolment.dart';
import 'package:greenhouse_app/services/sensor_provisioning_service.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import '../helpers/test_config.dart'; // existing helper building a ConnectionConfig

void main() {
  const sensor = SensorEnrolment(
      mac: '206EF16C9DB0', key: '000102030405060708090a0b0c0d0e0f');

  test('enrol posts mac, key, zone and role', () async {
    late http.Request seen;
    final svc = SensorProvisioningService(
      config: testConfig(apiToken: 'tok'),
      client: MockClient((req) async {
        seen = req;
        return http.Response('{"mac":"206EF16C9DB0"}', 201);
      }),
    );
    await svc.enrol(sensor: sensor, zone: 'zone5', name: 'Basil', sleepy: true);
    final body = jsonDecode(seen.body) as Map<String, dynamic>;
    expect(body['mac'], '206EF16C9DB0');
    expect(body['zone'], 'zone5');
    expect(body['sleepy'], true);
    expect(seen.headers['Authorization'], 'Bearer tok');
  });

  test('enrol throws when the Pi rejects the request', () async {
    final svc = SensorProvisioningService(
      config: testConfig(apiToken: 'tok'),
      client: MockClient((_) async => http.Response('{"error":"bad"}', 400)),
    );
    expect(
        () => svc.enrol(
            sensor: sensor, zone: 'z', name: 'n', sleepy: false),
        throwsA(isA<http.ClientException>()));
  });

  test('unenrolled fetches the list', () async {
    final svc = SensorProvisioningService(
      config: testConfig(apiToken: 'tok'),
      client: MockClient((_) async =>
          http.Response('{"unenrolled":["206EF16C9DB0"]}', 200)),
    );
    expect(await svc.unenrolled(), ['206EF16C9DB0']);
  });

  test('remove issues DELETE', () async {
    late http.Request seen;
    final svc = SensorProvisioningService(
      config: testConfig(apiToken: 'tok'),
      client: MockClient((req) async {
        seen = req;
        return http.Response('', 204);
      }),
    );
    await svc.remove('112233445566');
    expect(seen.method, 'DELETE');
    expect(seen.url.pathSegments.last, '112233445566');
  });
}
