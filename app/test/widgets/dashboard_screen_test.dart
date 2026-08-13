import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/screens/dashboard/dashboard_screen.dart';
import 'package:greenhouse_app/services/zone_plant_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<void> _pumpDashboard(WidgetTester tester) async {
  await tester.pumpWidget(ProviderScope(
    overrides: [
      readingsProvider.overrideWith((ref) => Stream.value({
            'zone1': {'soil/moisture': 50.0},
          })),
    ],
    child: const MaterialApp(home: DashboardScreen()),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('a zone with a stored plant assignment shows its status badge',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    // Pre-seed the store directly (mirrors how the real app would have an
    // assignment already saved from a previous session) so this test proves
    // DashboardScreen actually reads zonePlantProvider and passes it down,
    // not just that ZoneCard can render one when handed it directly.
    await const ZonePlantStore()
        .setAssignment('zone1', const ZonePlantAssignment(profileId: 'tomato'));

    await _pumpDashboard(tester);

    final button = tester.widget<IconButton>(find.byKey(const Key('zone_care_button')));
    expect(button.tooltip, contains('Tomato'));
  });

  testWidgets('a zone with no stored assignment shows the "set a plant" invitation',
      (tester) async {
    SharedPreferences.setMockInitialValues({});

    await _pumpDashboard(tester);

    final button = tester.widget<IconButton>(find.byKey(const Key('zone_care_button')));
    expect(button.tooltip, 'Set a plant for this zone');
  });

  testWidgets('tapping the care button opens the zone care sheet for that zone',
      (tester) async {
    SharedPreferences.setMockInitialValues({});

    await _pumpDashboard(tester);
    await tester.tap(find.byKey(const Key('zone_care_button')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('zone-care-plant-dropdown')), findsOneWidget);
  });
}
