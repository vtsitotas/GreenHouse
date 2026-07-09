import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/providers/history_provider.dart';
import 'package:greenhouse_app/services/history_service.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

class MockHistoryService extends Mock implements HistoryService {}

class MockPairingService extends Mock implements PairingService {}

void main() {
  test('HistoryQuery infers kind from zone when not given explicitly', () {
    expect(const HistoryQuery(zone: 'zone1', metric: 'air_temperature').kind, 'zone');
    expect(const HistoryQuery(zone: null, metric: 'temperature').kind, 'weather');
  });

  test('HistoryQuery equality considers zone, kind, metric, and hours', () {
    const a = HistoryQuery(zone: 'zone1', metric: 'air_temperature', hours: 24);
    const b = HistoryQuery(zone: 'zone1', metric: 'air_temperature', hours: 24);
    const c = HistoryQuery(zone: 'zone1', metric: 'air_temperature', hours: 168);
    expect(a, b);
    expect(a == c, isFalse);
  });

  test('HistoryQuery.isCustomRange is true only when both since and until are set', () {
    final since = DateTime.fromMillisecondsSinceEpoch(0);
    final until = DateTime.fromMillisecondsSinceEpoch(1000);
    expect(const HistoryQuery(metric: 'temperature').isCustomRange, isFalse);
    expect(HistoryQuery(metric: 'temperature', since: since).isCustomRange, isFalse);
    expect(HistoryQuery(metric: 'temperature', since: since, until: until).isCustomRange, isTrue);
  });

  test('HistoryQuery equality also considers since and until', () {
    final since = DateTime.fromMillisecondsSinceEpoch(0);
    final until = DateTime.fromMillisecondsSinceEpoch(1000);
    final a = HistoryQuery(metric: 'temperature', since: since, until: until);
    final b = HistoryQuery(metric: 'temperature', since: since, until: until);
    final c = HistoryQuery(metric: 'temperature', since: since, until: until.add(const Duration(seconds: 1)));
    expect(a, b);
    expect(a == c, isFalse);
  });

  test('historyPointsProvider passes kind/hours/zone through to HistoryService', () async {
    final mockService = MockHistoryService();
    final mockPairing = MockPairingService();
    when(() => mockPairing.loadConfig()).thenAnswer((_) async => const ConnectionConfig(
          lanHost: 'greenhouse.local',
          remoteHost: '',
          port: 8883,
          tlsFingerprint: '',
          username: '',
          password: '',
          remoteUsername: '',
          remotePassword: '',
        ));
    when(() => mockService.fetchPoints(
          lanHost: any(named: 'lanHost'),
          zone: any(named: 'zone'),
          kind: any(named: 'kind'),
          metric: any(named: 'metric'),
          hours: any(named: 'hours'),
        )).thenAnswer((_) async => [
          HistoryPoint(time: DateTime.fromMillisecondsSinceEpoch(0), avg: 1, min: 1, max: 1),
        ]);

    final container = ProviderContainer(overrides: [
      historyServiceProvider.overrideWithValue(mockService),
      pairingServiceProvider.overrideWithValue(mockPairing),
    ]);
    addTearDown(container.dispose);

    const query = HistoryQuery(zone: null, kind: 'weather', metric: 'temperature', hours: 168);
    final result = await container.read(historyPointsProvider(query).future);

    expect(result.length, 1);
    verify(() => mockService.fetchPoints(
          lanHost: 'greenhouse.local',
          zone: null,
          kind: 'weather',
          metric: 'temperature',
          hours: 168,
        )).called(1);
  });

  test('historyPointsProvider forwards since/until to HistoryService for a custom range', () async {
    final mockService = MockHistoryService();
    final mockPairing = MockPairingService();
    when(() => mockPairing.loadConfig()).thenAnswer((_) async => const ConnectionConfig(
          lanHost: 'greenhouse.local',
          remoteHost: '',
          port: 8883,
          tlsFingerprint: '',
          username: '',
          password: '',
          remoteUsername: '',
          remotePassword: '',
        ));
    when(() => mockService.fetchPoints(
          lanHost: any(named: 'lanHost'),
          zone: any(named: 'zone'),
          kind: any(named: 'kind'),
          metric: any(named: 'metric'),
          hours: any(named: 'hours'),
          since: any(named: 'since'),
          until: any(named: 'until'),
        )).thenAnswer((_) async => []);

    final container = ProviderContainer(overrides: [
      historyServiceProvider.overrideWithValue(mockService),
      pairingServiceProvider.overrideWithValue(mockPairing),
    ]);
    addTearDown(container.dispose);

    final since = DateTime.fromMillisecondsSinceEpoch(1000000);
    final until = DateTime.fromMillisecondsSinceEpoch(2000000);
    final query = HistoryQuery(zone: 'zone1', metric: 'air_temperature', since: since, until: until);
    await container.read(historyPointsProvider(query).future);

    verify(() => mockService.fetchPoints(
          lanHost: 'greenhouse.local',
          zone: 'zone1',
          kind: 'zone',
          metric: 'air_temperature',
          hours: 24,
          since: since,
          until: until,
        )).called(1);
  });

  group('historyWithPredictionProvider', () {
    HistoryPoint pt(int epochSeconds, double avg) => HistoryPoint(
          time: DateTime.fromMillisecondsSinceEpoch(epochSeconds * 1000),
          avg: avg,
          min: avg,
          max: avg,
        );

    test('returns no prediction when fewer than 2 actual points', () async {
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20)]),
      ]);
      addTearDown(container.dispose);
      const query = HistoryQuery(zone: 'zone1', metric: 'air_temperature', hours: 24);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.actual.length, 1);
      expect(data.predicted, isEmpty);
    });

    test('uses forecast overlay for weather temperature when available', () async {
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20), pt(60, 21)]),
        forecastProvider.overrideWith((ref) => Stream.value({
              'times': [DateTime.fromMillisecondsSinceEpoch(120000).toUtc().toIso8601String()],
              'temps': [25.0],
              'precip': [0.0],
            })),
      ]);
      addTearDown(container.dispose);
      const query = HistoryQuery(zone: null, kind: 'weather', metric: 'temperature', hours: 24);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.predicted, isNotEmpty);
      expect(data.predicted.first.avg, 25.0);
    });

    test('falls back to trend extrapolation for zone metrics (no forecast)', () async {
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20), pt(60, 22)]),
      ]);
      addTearDown(container.dispose);
      const query = HistoryQuery(zone: 'zone1', metric: 'soil_moisture', hours: 24);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.predicted, isNotEmpty);
      expect(data.predicted.first.avg, greaterThan(22.0));
    });

    test('never predicts for a custom (since/until) range, even with enough points', () async {
      final since = DateTime.fromMillisecondsSinceEpoch(0);
      final until = DateTime.fromMillisecondsSinceEpoch(120000);
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20), pt(60, 22), pt(120, 24)]),
      ]);
      addTearDown(container.dispose);
      final query = HistoryQuery(zone: 'zone1', metric: 'air_temperature', since: since, until: until);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.actual.length, 3);
      expect(data.predicted, isEmpty);
    });

    test('still predicts for a normal rolling-window query with enough points (regression check)',
        () async {
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20), pt(60, 22)]),
      ]);
      addTearDown(container.dispose);
      const query = HistoryQuery(zone: 'zone1', metric: 'soil_moisture', hours: 24);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.predicted, isNotEmpty);
    });
  });
}
