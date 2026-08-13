import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/screens/dashboard/zone_card.dart';
import 'package:greenhouse_app/services/zone_plant_store.dart';

void main() {
  testWidgets('ZoneCard displays zone name and temperature', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'air/temperature': 24.5, 'air/humidity': 60.0},
    ))));
    expect(find.text('Zone 1'), findsOneWidget);
    expect(find.textContaining('24.5'), findsOneWidget);
  });

  testWidgets('ZoneCard shows warning icon when soil moisture < 30%', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': 15.0},
    ))));
    expect(find.byIcon(Icons.warning_amber_rounded), findsOneWidget);
  });

  testWidgets('the low-soil warning explains itself instead of just alarming',
      (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': 15.0},
    ))));

    // A bare triangle says "something is wrong" without saying what.
    expect(find.text('Dry — may need watering'), findsOneWidget);
    // find.byType(Tooltip) alone would now also match the plant-care
    // button's tooltip -- match on content instead of relying on there
    // being exactly one Tooltip in the tree.
    final tooltip = tester.widget<Tooltip>(
      find.byWidgetPredicate(
          (w) => w is Tooltip && (w.message?.startsWith('Soil moisture is below') ?? false)),
    );
    expect(tooltip.message, contains('30%'));
    expect(tooltip.message, contains('watering'));
  });

  testWidgets('the threshold quoted to the user is the one that fired',
      (tester) async {
    // Just below the constant warns; exactly at it does not. Locks the text
    // and the comparison together so they cannot drift.
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': kLowSoilMoisturePct - 0.1},
    ))));
    expect(find.byIcon(Icons.warning_amber_rounded), findsOneWidget);

    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': kLowSoilMoisturePct},
    ))));
    expect(find.byIcon(Icons.warning_amber_rounded), findsNothing);
  });

  testWidgets('ZoneCard does not show warning icon when soil moisture >= 30%', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': 45.0},
    ))));
    expect(find.byIcon(Icons.warning_amber_rounded), findsNothing);
  });

  testWidgets('tapping the Temp chip navigates to air_temperature history', (tester) async {
    String? lastLocation;
    final router = GoRouter(
      initialLocation: '/',
      routes: [
        GoRoute(
          path: '/',
          builder: (_, __) => const Scaffold(
            body: ZoneCard(zone: 'zone1', readings: {'air/temperature': 24.5}),
          ),
        ),
        GoRoute(
          path: '/history/:zone/:metric',
          builder: (_, state) {
            lastLocation = '/history/${state.pathParameters['zone']}/${state.pathParameters['metric']}';
            return const SizedBox.shrink();
          },
        ),
      ],
    );
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.tap(find.byKey(const Key('chip_air_temperature')));
    await tester.pumpAndSettle();
    expect(lastLocation, '/history/zone1/air_temperature');
  });

  testWidgets('tapping the Soil chip navigates to soil_moisture history', (tester) async {
    String? lastLocation;
    final router = GoRouter(
      initialLocation: '/',
      routes: [
        GoRoute(
          path: '/',
          builder: (_, __) => const Scaffold(
            body: ZoneCard(zone: 'zone1', readings: {'soil/moisture': 45.0}),
          ),
        ),
        GoRoute(
          path: '/history/:zone/:metric',
          builder: (_, state) {
            lastLocation = '/history/${state.pathParameters['zone']}/${state.pathParameters['metric']}';
            return const SizedBox.shrink();
          },
        ),
      ],
    );
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.tap(find.byKey(const Key('chip_soil_moisture')));
    await tester.pumpAndSettle();
    expect(lastLocation, '/history/zone1/soil_moisture');
  });

  testWidgets('with no plant assigned, the care button invites setting one',
      (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {},
    ))));

    final button = tester.widget<IconButton>(find.byKey(const Key('zone_care_button')));
    expect(button.tooltip, 'Set a plant for this zone');
    expect(find.byIcon(Icons.eco_outlined), findsOneWidget);
    expect(find.byKey(const Key('zone_care_advice')), findsNothing);
  });

  testWidgets('a plant within its ideal range shows a good badge and no advice line',
      (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': 50.0}, // inside tomato's 40-70 range
      plantAssignment: ZonePlantAssignment(profileId: 'tomato'),
    ))));

    final button = tester.widget<IconButton>(find.byKey(const Key('zone_care_button')));
    expect(button.tooltip, contains('Tomato'));
    expect(find.byKey(const Key('zone_care_advice')), findsNothing);
  });

  testWidgets('soil drier than the assigned plant likes shows a named, actionable line',
      (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': 10.0}, // below tomato's 40% floor
      plantAssignment: ZonePlantAssignment(profileId: 'tomato'),
    ))));

    expect(find.byKey(const Key('zone_care_advice')), findsOneWidget);
    final text = tester.widget<Text>(find.byKey(const Key('zone_care_advice')));
    expect(text.data, contains('Tomato'));
    expect(text.data, contains('drier'));
  });

  testWidgets('tapping the care button calls the supplied callback', (tester) async {
    var tapped = false;
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: const {},
      onCareTap: () => tapped = true,
    ))));

    await tester.tap(find.byKey(const Key('zone_care_button')));
    expect(tapped, isTrue);
  });
}
