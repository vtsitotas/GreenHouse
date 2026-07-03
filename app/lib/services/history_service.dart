import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:greenhouse_app/models/history_point.dart';

class HistoryService {
  final http.Client client;
  HistoryService({http.Client? client}) : client = client ?? http.Client();

  Future<List<HistoryPoint>> fetchPoints({
    required String lanHost,
    String? zone,
    String? kind,
    required String metric,
    double hours = 24,
  }) async {
    final params = <String, String>{
      'metric': metric,
      'hours': hours.toString(),
      if (zone != null) 'zone': zone,
      if (kind != null) 'kind': kind,
    };
    final uri = Uri.http(lanHost, '/api/history', params);
    final resp = await client.get(uri);
    if (resp.statusCode != 200) {
      throw Exception('History fetch failed: ${resp.statusCode}');
    }
    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    final points = (data['points'] as List).cast<List<dynamic>>();
    return points.map(HistoryPoint.fromJson).toList();
  }

  Future<List<HistorySeries>> fetchSeries({required String lanHost}) async {
    final uri = Uri.http(lanHost, '/api/history/series');
    final resp = await client.get(uri);
    if (resp.statusCode != 200) {
      throw Exception('Series fetch failed: ${resp.statusCode}');
    }
    final data = jsonDecode(resp.body) as List;
    return data.cast<Map<String, dynamic>>().map(HistorySeries.fromJson).toList();
  }
}
