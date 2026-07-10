import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/models/notification_settings.dart';
import 'package:greenhouse_app/models/weather_rule.dart';
import 'package:greenhouse_app/models/weather_alert.dart';
import 'package:greenhouse_app/providers/actuators_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/screens/weather/weather_screen.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';

class MockConnection extends Mock implements GreenhouseConnection {}

void main() {
  setUpAll(() {
    registerFallbackValue(const ConnectionConfig(
      lanHost: '', remoteHost: '', port: 9001,
      tlsFingerprint: '', username: '', password: '',
      remoteUsername: '', remotePassword: '',
    ));
  });

  late MockConnection conn;

  setUp(() {
    conn = MockConnection();
    when(() => conn.events).thenAnswer((_) => const Stream.empty());
    when(() => conn.status).thenAnswer((_) => const Stream.empty());
    when(() => conn.disconnect()).thenAnswer((_) async {});
    when(() => conn.publishRaw(any(), any(), retain: any(named: 'retain')))
        .thenAnswer((_) async {});
  });

  const existingRule = WeatherRule(
    id: 'zone1-dry', name: 'Zone 1 soil dry', enabled: true, notify: true,
    zone: 'zone1', metric: 'soil_moisture', op: '<', value: 15.0,
    durationMinutes: 2880, actuatorId: null, command: null,
  );

  // repositoryProvider stays overridden (routed through the mocked
  // connection) so that user actions — which call
  // ref.read(repositoryProvider).publishRules(...) directly — are
  // verifiable via conn.publishRaw. rulesProvider/forecastProvider/
  // weatherAlertsProvider are overridden *directly* for initial display,
  // sidestepping GreenhouseRepository's cached-then-live stream getters
  // (whose cache-replay only fires for non-empty lists/already-arrived
  // events — an unrelated synchronization detail that would otherwise
  // leave these providers stuck in AsyncLoading forever, and an
  // indeterminate CircularProgressIndicator never lets pumpAndSettle()
  // finish).
  List<Override> baseOverrides({required List<WeatherRule> rules}) => [
        repositoryProvider.overrideWith((ref) => GreenhouseRepository(connection: conn)),
        rulesProvider.overrideWith((ref) => Stream.value(rules)),
        readingsProvider.overrideWith((ref) => Stream.value({
              'zone1': {'soil_moisture': 12.0},
            })),
        actuatorsProvider.overrideWith((ref) => Stream.value(<String, ActuatorState>{})),
        notificationSettingsProvider.overrideWith(
            (ref) => Stream.value(const NotificationSettings(frostForecast: true, dailySummary: true))),
        forecastProvider.overrideWith((ref) => Stream.value(<String, dynamic>{})),
        weatherAlertsProvider.overrideWith((ref) => const Stream<WeatherAlert>.empty()),
      ];

  testWidgets('toggling a rule notify switch publishes updated rules', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: baseOverrides(rules: [existingRule]),
      child: const MaterialApp(home: WeatherScreen()),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Rules'));
    await tester.pumpAndSettle();

    expect(find.text('Zone 1 soil dry'), findsOneWidget);

    // The notify switch is the last Switch on the rule card (enabled switch
    // comes first in trailing order — see implementation step below).
    final notifySwitch = find.byKey(const Key('rule-notify-switch-zone1-dry'));
    expect(notifySwitch, findsOneWidget);
    await tester.tap(notifySwitch);
    await tester.pumpAndSettle();

    verify(() => conn.publishRaw('greenhouse/rules/update', any(), retain: true)).called(greaterThan(0));
  });

  testWidgets('alert settings toggles publish notification settings', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: baseOverrides(rules: const []),
      child: const MaterialApp(home: WeatherScreen()),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Rules'));
    await tester.pumpAndSettle();

    final frostSwitch = find.byKey(const Key('alert-settings-frost-switch'));
    expect(frostSwitch, findsOneWidget);
    await tester.tap(frostSwitch);
    await tester.pumpAndSettle();

    verify(() => conn.publishRaw(
          'greenhouse/settings/notifications',
          any(),
          retain: true,
        )).called(1);
  });
}
