// app/lib/services/sensor_provisioning_service.dart
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import '../models/connection_config.dart';
import '../models/sensor_enrolment.dart';
import '../utils/cert_pinning.dart';
import 'history_service.dart' show kPortalHttpsPort;

/// Talks to the Pi's `/api/nodes` (add/list/remove a sensor). Mirrors
/// [HistoryService]'s transport exactly -- pinned HTTPS first, falling back
/// to plaintext only on failure -- since this hits the same portal over the
/// same LAN link, just a different endpoint.
class SensorProvisioningService {
  SensorProvisioningService({required this.config, http.Client? client})
      : _client = client;

  final ConnectionConfig config;

  /// Injected only by tests. In production each request builds a client
  /// whose TLS trust is pinned to the paired unit's fingerprint.
  final http.Client? _client;

  static Uri _httpsUri(String lanHost, String path) =>
      Uri(scheme: 'https', host: lanHost, port: kPortalHttpsPort, path: path);

  static Uri _httpUri(String lanHost, String path) => Uri.http(lanHost, path);

  Map<String, String> get _headers => {
        if (config.apiToken.isNotEmpty)
          'Authorization': 'Bearer ${config.apiToken}',
        'Content-Type': 'application/json',
      };

  /// Fetch over TLS (pinned) when possible, falling back to plaintext --
  /// same reasoning as [HistoryService._get]: without the fallback, updating
  /// the app before the Pi would break sensor enrolment entirely.
  Future<http.Response> _request(
    String path,
    Future<http.Response> Function(http.Client client, Uri uri) send,
  ) async {
    final plainUri = _httpUri(config.lanHost, path);
    if (_client != null) {
      // Test seam: exercise the plaintext URL shape deterministically.
      return send(_client, plainUri);
    }
    if (config.tlsFingerprint.isNotEmpty) {
      final secure = IOClient(pinnedHttpClient(config.tlsFingerprint,
          timeout: const Duration(seconds: 5)));
      try {
        return await send(secure, _httpsUri(config.lanHost, path));
      } catch (_) {
        // Unreachable/TLS-refused (or a pin mismatch) -> fall through.
      } finally {
        secure.close();
      }
    }
    final plain = http.Client();
    try {
      return await send(plain, plainUri);
    } finally {
      plain.close();
    }
  }

  Future<void> enrol({
    required SensorEnrolment sensor,
    required String zone,
    required String name,
    required bool sleepy,
  }) async {
    final body = jsonEncode({
      'mac': sensor.mac,
      'key': sensor.key,
      'zone': zone,
      'name': name,
      'sleepy': sleepy,
    });
    final res = await _request(
        '/api/nodes', (c, uri) => c.post(uri, headers: _headers, body: body));
    if (res.statusCode != 201) {
      throw http.ClientException('Enrolment failed (${res.statusCode})');
    }
  }

  Future<List<String>> unenrolled() async {
    final res =
        await _request('/api/nodes', (c, uri) => c.get(uri, headers: _headers));
    if (res.statusCode != 200) return const [];
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    return (body['unenrolled'] as List? ?? const []).cast<String>();
  }

  Future<void> remove(String mac) async {
    final res = await _request(
        '/api/nodes/$mac', (c, uri) => c.delete(uri, headers: _headers));
    if (res.statusCode != 204) {
      throw http.ClientException('Removal failed (${res.statusCode})');
    }
  }
}
