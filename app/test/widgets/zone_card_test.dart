import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/screens/dashboard/zone_card.dart';

void main() {
  testWidgets('ZoneCard displays zone name and temperature', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'air/temperature': 24.5, 'air/humidity': 60.0},
    ))));
    expect(find.text('Zone 1'), findsOneWidget);
    expect(find.textContaining('24.5'), findsOneWidget);
  });

  testWidgets('ZoneCard shows warning icon when soil moisture < 30%', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': 15.0},
    ))));
    expect(find.byIcon(Icons.warning_amber_rounded), findsOneWidget);
  });

  testWidgets('ZoneCard does not show warning icon when soil moisture >= 30%', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ZoneCard(
      zone: 'zone1',
      readings: {'soil/moisture': 45.0},
    ))));
    expect(find.byIcon(Icons.warning_amber_rounded), findsNothing);
  });
}
