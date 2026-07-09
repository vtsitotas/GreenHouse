import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:greenhouse_app/services/history_service.dart';

void main() {
  group('HistoryService', () {
    test('fetchPoints parses a successful response', () async {
      final client = MockClient((request) async {
        expect(request.url.path, '/api/history');
        expect(request.url.queryParameters['zone'], 'zone1');
        expect(request.url.queryParameters['metric'], 'air_temperature');
        return http.Response(
          jsonEncode({
            'zone': 'zone1',
            'metric': 'air_temperature',
            'resolution': 'minute',
            'points': [
              [1000, 20.0, 19.0, 21.0],
              [1060, 22.0, 21.0, 23.0],
            ],
          }),
          200,
        );
      });
      final service = HistoryService(client: client);
      final points = await service.fetchPoints(
        lanHost: 'greenhouse.local',
        zone: 'zone1',
        metric: 'air_temperature',
        hours: 24,
      );
      expect(points.length, 2);
      expect(points[0].avg, 20.0);
      expect(points[1].max, 23.0);
    });

    test('fetchPoints throws on non-200 response', () async {
      final client = MockClient((request) async => http.Response('error', 500));
      final service = HistoryService(client: client);
      expect(
        () => service.fetchPoints(lanHost: 'greenhouse.local', zone: 'zone1', metric: 'x'),
        throwsA(isA<Exception>()),
      );
    });

    test('fetchPoints sends since/until instead of hours for a custom range', () async {
      final client = MockClient((request) async {
        expect(request.url.queryParameters['since'], '1000');
        expect(request.url.queryParameters['until'], '2000');
        expect(request.url.queryParameters.containsKey('hours'), isFalse);
        return http.Response(jsonEncode({'points': <List<dynamic>>[]}), 200);
      });
      final service = HistoryService(client: client);
      await service.fetchPoints(
        lanHost: 'greenhouse.local',
        zone: 'zone1',
        metric: 'air_temperature',
        since: DateTime.fromMillisecondsSinceEpoch(1000000),
        until: DateTime.fromMillisecondsSinceEpoch(2000000),
      );
    });

    test('fetchPoints sends hours (not since/until) when no custom range is given', () async {
      final client = MockClient((request) async {
        // hours defaults to 24 as a double; Dart's double.toString() renders
        // it as '24.0' -- this must stay exactly as it was before this task,
        // since changing wire format for the existing rolling-window path is
        // out of scope and would be an unrequested behavior change.
        expect(request.url.queryParameters['hours'], '24.0');
        expect(request.url.queryParameters.containsKey('since'), isFalse);
        expect(request.url.queryParameters.containsKey('until'), isFalse);
        return http.Response(jsonEncode({'points': <List<dynamic>>[]}), 200);
      });
      final service = HistoryService(client: client);
      await service.fetchPoints(lanHost: 'greenhouse.local', zone: 'zone1', metric: 'x');
    });

    test('fetchSeries parses a successful response', () async {
      final client = MockClient((request) async {
        expect(request.url.path, '/api/history/series');
        return http.Response(
          jsonEncode([
            {'kind': 'zone', 'zone': 'zone1', 'metric': 'air_temperature'},
            {'kind': 'weather', 'zone': null, 'metric': 'temperature'},
          ]),
          200,
        );
      });
      final service = HistoryService(client: client);
      final series = await service.fetchSeries(lanHost: 'greenhouse.local');
      expect(series.length, 2);
      expect(series[0].zone, 'zone1');
      expect(series[1].zone, isNull);
    });
  });
}
