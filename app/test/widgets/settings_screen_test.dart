import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/screens/settings/settings_screen.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

class MockPairingService extends Mock implements PairingService {}

const _config = ConnectionConfig(
  lanHost: 'greenhouse.local',
  remoteHost: '',
  port: 8883,
  tlsFingerprint: 'AA:BB:CC',
  username: 'app',
  password: 'pw',
  remoteUsername: '',
  remotePassword: '',
);

Future<void> _pumpSettings(WidgetTester tester, PairingService pairing) async {
  final router = GoRouter(
    initialLocation: '/settings',
    routes: [
      GoRoute(path: '/settings', builder: (_, __) => const SettingsScreen()),
      GoRoute(path: '/pair', builder: (_, __) => const Scaffold(body: Text('PAIR SCREEN'))),
    ],
  );
  await tester.pumpWidget(ProviderScope(
    overrides: [pairingServiceProvider.overrideWithValue(pairing)],
    child: MaterialApp.router(routerConfig: router),
  ));
  await tester.pump();
}

void main() {
  testWidgets('the safety code never shows a shell command to the user',
      (tester) async {
    final pairing = MockPairingService();
    when(() => pairing.loadConfig()).thenAnswer((_) async => _config);

    await _pumpSettings(tester, pairing);
    await tester.pump();

    // `sudo bash scripts/selftest.sh` used to be the subtitle here. It is an
    // operator instruction; it belongs in SECURITY.md, not in front of
    // whoever is holding the phone.
    expect(find.textContaining('sudo'), findsNothing);
    expect(find.textContaining('selftest'), findsNothing);
    expect(find.textContaining('identifies your greenhouse'), findsOneWidget);
  });

  testWidgets('forgetting the greenhouse asks first and can be cancelled',
      (tester) async {
    final pairing = MockPairingService();
    when(() => pairing.loadConfig()).thenAnswer((_) async => _config);
    when(() => pairing.clearConfig()).thenAnswer((_) async {});

    await _pumpSettings(tester, pairing);

    await tester.tap(find.text('Forget this greenhouse'));
    await tester.pumpAndSettle();
    expect(find.text('Forget this greenhouse?'), findsOneWidget);

    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();

    // The whole point: a single mis-tap must not wipe the pairing.
    verifyNever(() => pairing.clearConfig());
  });

  testWidgets('confirming actually clears the pairing and leaves the screen',
      (tester) async {
    final pairing = MockPairingService();
    when(() => pairing.loadConfig()).thenAnswer((_) async => _config);
    when(() => pairing.clearConfig()).thenAnswer((_) async {});

    await _pumpSettings(tester, pairing);

    await tester.tap(find.text('Forget this greenhouse'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Forget'));
    await tester.pumpAndSettle();

    verify(() => pairing.clearConfig()).called(1);
    expect(find.text('PAIR SCREEN'), findsOneWidget);
  });
}
