// app/lib/services/sensor_provisioning_service.dart
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/connection_config.dart';
import '../models/sensor_enrolment.dart';

class SensorProvisioningService {
  SensorProvisioningService({required this.config, http.Client? client})
      : _client = client ?? http.Client();

  final ConnectionConfig config;
  final http.Client _client;

  Uri _url(String path) =>
      Uri.parse('https://${config.host}:${config.port}$path');

  Map<String, String> get _headers => {
        'Authorization': 'Bearer ${config.apiToken}',
        'Content-Type': 'application/json',
      };

  Future<void> enrol({
    required SensorEnrolment sensor,
    required String zone,
    required String name,
    required bool sleepy,
  }) async {
    final res = await _client.post(_url('/api/nodes'),
        headers: _headers,
        body: jsonEncode({
          'mac': sensor.mac,
          'key': sensor.key,
          'zone': zone,
          'name': name,
          'sleepy': sleepy,
        }));
    if (res.statusCode != 201) {
      throw http.ClientException('Enrolment failed (${res.statusCode})');
    }
  }

  Future<List<String>> unenrolled() async {
    final res = await _client.get(_url('/api/nodes'), headers: _headers);
    if (res.statusCode != 200) return const [];
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    return (body['unenrolled'] as List? ?? const []).cast<String>();
  }

  Future<void> remove(String mac) async {
    final res = await _client.delete(_url('/api/nodes/$mac'), headers: _headers);
    if (res.statusCode != 204) {
      throw http.ClientException('Removal failed (${res.statusCode})');
    }
  }
}
