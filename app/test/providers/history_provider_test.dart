import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/history_point.dart';
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
}
