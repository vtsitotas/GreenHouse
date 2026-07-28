import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/utils/cert_pinning.dart';

/// Port the Pi's portal serves HTTPS on. Plain HTTP stays on 80 for the
/// captive portal, which can't be encrypted without breaking the OS popup.
const kPortalHttpsPort = 8443;

class HistoryService {
  /// Injected only by tests. In production each request builds a client whose
  /// TLS trust is pinned to the paired unit's fingerprint.
  final http.Client? client;
  HistoryService({this.client});

  /// Bearer header for the Pi's history API. Omitted entirely when no token is
  /// configured, so the server's 401 (not a malformed header) is what surfaces.
  static Map<String, String> authHeaders(String apiToken) =>
      apiToken.isEmpty ? const {} : {'Authorization': 'Bearer $apiToken'};

  /// HTTPS URL for a history path, used when a pinned fingerprint is known.
  static Uri httpsUri(String lanHost, String path, [Map<String, String>? q]) =>
      Uri(scheme: 'https', host: lanHost, port: kPortalHttpsPort, path: path,
          queryParameters: q);

  static Uri httpUri(String lanHost, String path, [Map<String, String>? q]) =>
      Uri.http(lanHost, path, q);

  /// Fetch over TLS (pinned) when possible, falling back to plaintext.
  ///
  /// The fallback exists so a phone on this build still reaches a Pi that
  /// hasn't been redeployed yet — without it, updating the app before the Pi
  /// would break history entirely. It is a real downgrade path, which is why
  /// the Pi side can refuse plaintext outright once every paired phone is
  /// updated (touch /etc/greenhouse/require_https; see portal.py).
  Future<http.Response> _get(
    String lanHost,
    String path,
    Map<String, String>? params,
    String apiToken,
    String tlsFingerprint,
  ) async {
    final headers = authHeaders(apiToken);
    if (client != null) {
      // Test seam: exercise the plaintext URL shape deterministically.
      return client!.get(httpUri(lanHost, path, params), headers: headers);
    }
    if (tlsFingerprint.isNotEmpty) {
      final secure = IOClient(pinnedHttpClient(
          tlsFingerprint, timeout: const Duration(seconds: 5)));
      try {
        return await secure.get(httpsUri(lanHost, path, params), headers: headers);
      } catch (_) {
        // Unreachable/TLS-refused → fall through to plaintext below. A pin
        // MISMATCH also lands here, so the fallback must not be treated as
        // "secure"; it is only ever a compatibility path.
      } finally {
        secure.close();
      }
    }
    final plain = http.Client();
    try {
      return await plain.get(httpUri(lanHost, path, params), headers: headers);
    } finally {
      plain.close();
    }
  }

  Future<List<HistoryPoint>> fetchPoints({
    required String lanHost,
    String? zone,
    String? kind,
    required String metric,
    double hours = 24,
    DateTime? since,
    DateTime? until,
    String apiToken = '',
    String tlsFingerprint = '',
  }) async {
    final isCustomRange = since != null && until != null;
    final params = <String, String>{
      'metric': metric,
      if (zone != null) 'zone': zone,
      if (kind != null) 'kind': kind,
      if (isCustomRange) 'since': (since.millisecondsSinceEpoch ~/ 1000).toString(),
      if (isCustomRange) 'until': (until.millisecondsSinceEpoch ~/ 1000).toString(),
      if (!isCustomRange) 'hours': hours.toString(),
    };
    final resp = await _get(lanHost, '/api/history', params, apiToken, tlsFingerprint);
    if (resp.statusCode != 200) {
      throw Exception('History fetch failed: ${resp.statusCode}');
    }
    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    return parsePoints(data);
  }

  /// Shared parsing for the `{points: [[ts, avg, min, max], ...]}` shape
  /// returned by both the HTTP /api/history endpoint and the MQTT
  /// greenhouse/history/response/<id> payload.
  static List<HistoryPoint> parsePoints(Map<String, dynamic> data) {
    final points = (data['points'] as List).cast<List<dynamic>>();
    return points.map(HistoryPoint.fromJson).toList();
  }

  Future<List<HistorySeries>> fetchSeries({
    required String lanHost,
    String apiToken = '',
    String tlsFingerprint = '',
  }) async {
    final resp =
        await _get(lanHost, '/api/history/series', null, apiToken, tlsFingerprint);
    if (resp.statusCode != 200) {
      throw Exception('Series fetch failed: ${resp.statusCode}');
    }
    final data = jsonDecode(resp.body) as List;
    return data.cast<Map<String, dynamic>>().map(HistorySeries.fromJson).toList();
  }
}
