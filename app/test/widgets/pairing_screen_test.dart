import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/screens/pairing/pairing_screen.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

class MockPairingService extends Mock implements PairingService {}

void main() {
  setUpAll(() => registerFallbackValue(const ConnectionConfig(
    lanHost: '', remoteHost: '', port: 9001,
    tlsFingerprint: '', username: '', password: '',
    remoteUsername: '', remotePassword: '',
  )));

  Future<void> pumpPairing(WidgetTester tester) async {
    final mock = MockPairingService();
    when(() => mock.saveConfig(any())).thenAnswer((_) async {});
    await tester.pumpWidget(ProviderScope(
      overrides: [pairingServiceProvider.overrideWithValue(mock)],
      child: const MaterialApp(home: PairingScreen()),
    ));
  }

  testWidgets('PairingScreen shows manual entry fields', (tester) async {
    await pumpPairing(tester);
    expect(find.text('Connect to your greenhouse'), findsOneWidget);
    expect(find.byType(TextFormField), findsWidgets);
    expect(find.text('Scan the QR code on the hub'), findsOneWidget);
  });

  testWidgets('the manual section keeps every advanced field, with hints',
      (tester) async {
    await pumpPairing(tester);

    // The section is collapsed by default -- most users only ever scan.
    expect(find.text('Security fingerprint'), findsNothing);

    await tester.tap(find.textContaining('Manual setup'));
    await tester.pumpAndSettle();

    // Every field stays reachable: this is the only path that works when
    // automatic discovery can't (e.g. the phone is itself the hotspot).
    for (final label in [
      'Address for access from away',
      'Username for access from away',
      'Password for access from away',
      'Port',
      'Username',
      'Security fingerprint',
      'History access key',
      'Camera key',
    ]) {
      expect(find.text(label), findsOneWidget, reason: 'missing field: $label');
    }

    // ...and each now says what it is for, instead of naming a protocol.
    expect(find.textContaining('automatically by the QR code'), findsOneWidget);
    expect(find.text('TLS fingerprint'), findsNothing);
    expect(find.textContaining('HiveMQ'), findsNothing);
  });
}
