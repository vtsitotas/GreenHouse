import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/screens/dashboard/zone_care_sheet.dart';
import 'package:greenhouse_app/services/pairing_service.dart';
import 'package:greenhouse_app/services/zone_plant_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

class MockPairingService extends Mock implements PairingService {}

Future<void> _pumpSheet(
  WidgetTester tester, {
  Map<String, double> readings = const {},
}) async {
  SharedPreferences.setMockInitialValues({});
  final pairing = MockPairingService();
  // No stored config -> historyPointsProvider's `config == null` branch
  // returns [] immediately, so the History section renders without needing
  // a live MQTT/HTTP connection in this test.
  when(() => pairing.loadConfig()).thenAnswer((_) async => null);

  await tester.pumpWidget(ProviderScope(
    overrides: [
      pairingServiceProvider.overrideWithValue(pairing),
      readingsProvider.overrideWith((ref) => Stream.value({'zone1': readings})),
    ],
    child: const MaterialApp(home: Scaffold(body: ZoneCareSheet(zone: 'zone1'))),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('starts with no plant selected', (tester) async {
    await _pumpSheet(tester);
    expect(find.text('Not set'), findsOneWidget);
    expect(find.text('Current status'), findsNothing);
  });

  testWidgets('picking a built-in plant shows its current status against live readings',
      (tester) async {
    await _pumpSheet(tester, readings: {'soil/moisture': 10.0}); // dry for a tomato

    await tester.tap(find.byKey(const Key('zone-care-plant-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Tomato').last);
    await tester.pumpAndSettle();

    expect(find.text('Current status'), findsOneWidget);
    expect(find.textContaining('drier'), findsOneWidget);
  });

  testWidgets('picking Custom reveals editable min/max fields per metric', (tester) async {
    await _pumpSheet(tester);

    await tester.tap(find.byKey(const Key('zone-care-plant-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Custom').last);
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('zone-care-custom-soil_moisture')), findsOneWidget);
    expect(find.byKey(const Key('zone-care-custom-air_temperature')), findsOneWidget);
    expect(find.byKey(const Key('zone-care-custom-air_humidity')), findsOneWidget);
  });

  testWidgets('saving a built-in pick persists it to the store', (tester) async {
    await _pumpSheet(tester);

    await tester.tap(find.byKey(const Key('zone-care-plant-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Basil').last);
    await tester.pumpAndSettle();

    // The sheet's content can be taller than the test surface once a
    // profile's Current status/History/Suggested rules sections render, so
    // the Save button may start out scrolled past the visible viewport.
    await tester.ensureVisible(find.byKey(const Key('zone-care-save-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('zone-care-save-button')));
    await tester.pumpAndSettle();

    final stored = (await const ZonePlantStore().load())['zone1'];
    expect(stored?.profileId, 'basil');
  });

  testWidgets('a metric outside the ideal range offers a suggested rule to add',
      (tester) async {
    await _pumpSheet(tester, readings: {'soil/moisture': 5.0}); // well below any profile's floor

    await tester.tap(find.byKey(const Key('zone-care-plant-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Tomato').last);
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('zone-care-suggest-soil_moisture')), findsOneWidget);
    expect(find.textContaining('Alert when Soil moisture'), findsOneWidget);
  });

  testWidgets('a metric inside the ideal range offers no suggested rule',
      (tester) async {
    await _pumpSheet(tester, readings: {'soil/moisture': 50.0}); // inside tomato's range

    await tester.tap(find.byKey(const Key('zone-care-plant-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Tomato').last);
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('zone-care-suggest-soil_moisture')), findsNothing);
  });
}
