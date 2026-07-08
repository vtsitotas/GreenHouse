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

  testWidgets('PairingScreen shows manual entry fields', (tester) async {
    final mock = MockPairingService();
    when(() => mock.saveConfig(any())).thenAnswer((_) async {});
    await tester.pumpWidget(ProviderScope(
      overrides: [pairingServiceProvider.overrideWithValue(mock)],
      child: const MaterialApp(home: PairingScreen()),
    ));
    expect(find.text('Connect to your greenhouse'), findsOneWidget);
    expect(find.byType(TextFormField), findsWidgets);
    expect(find.text('Scan QR code'), findsOneWidget);
  });
}
