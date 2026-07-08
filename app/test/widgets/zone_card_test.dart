import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/screens/dashboard/zone_card.dart';

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
          builder: (_, __) => Scaffold(
            body: ZoneCard(zone: 'zone1', readings: const {'air/temperature': 24.5}),
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
          builder: (_, __) => Scaffold(
            body: ZoneCard(zone: 'zone1', readings: const {'soil/moisture': 45.0}),
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
}
