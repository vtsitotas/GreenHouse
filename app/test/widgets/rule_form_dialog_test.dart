import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/weather_rule.dart';
import 'package:greenhouse_app/screens/weather/rule_form_dialog.dart';

void main() {
  Future<WeatherRule?> openDialog(WidgetTester tester, {WeatherRule? existing}) async {
    WeatherRule? result;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(builder: (context) {
          return ElevatedButton(
            onPressed: () async {
              result = await showRuleFormDialog(
                context,
                existing: existing,
                zones: const ['zone1', 'zone2'],
                actuatorIds: const ['fan1', 'pump1'],
              );
            },
            child: const Text('Open'),
          );
        }),
      ),
    ));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    return result;
  }

  testWidgets('selecting a zone requires a duration before Save is enabled', (tester) async {
    await openDialog(tester);

    await tester.enterText(find.widgetWithText(TextField, 'Name'), 'Zone 1 dry');
    await tester.tap(find.byKey(const Key('rule-form-zone-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Zone 1').last);
    await tester.pumpAndSettle();

    // Duration field appears and is required once a zone is selected.
    expect(find.widgetWithText(TextField, 'Duration (minutes)'), findsOneWidget);

    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    // Dialog is still open — Save was a no-op because duration is empty.
    expect(find.text('Save'), findsOneWidget);
  });

  testWidgets('creating an alert-only zone rule returns the expected WeatherRule', (tester) async {
    WeatherRule? result;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(builder: (context) {
          return ElevatedButton(
            onPressed: () async {
              result = await showRuleFormDialog(
                context,
                zones: const ['zone1', 'zone2'],
                actuatorIds: const ['fan1', 'pump1'],
              );
            },
            child: const Text('Open'),
          );
        }),
      ),
    ));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();

    await tester.enterText(find.widgetWithText(TextField, 'Name'), 'Zone 1 dry');
    await tester.tap(find.byKey(const Key('rule-form-zone-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Zone 1').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('rule-form-metric-dropdown')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Soil moisture (%)').last);
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Threshold value'), '15');
    await tester.enterText(find.widgetWithText(TextField, 'Duration (minutes)'), '2880');

    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();

    expect(result, isNotNull);
    expect(result!.zone, 'zone1');
    expect(result!.metric, 'soil_moisture');
    expect(result!.value, 15.0);
    expect(result!.durationMinutes, 2880);
    expect(result!.actuatorId, isNull); // "Also control a device" left off
  });

  testWidgets('editing an existing rule pre-fills its fields', (tester) async {
    const existing = WeatherRule(
      id: 'zone2-humid', name: 'Zone 2 too humid', enabled: true, notify: true,
      zone: 'zone2', metric: 'air_humidity', op: '>', value: 70.0,
      durationMinutes: 1440, actuatorId: null, command: null,
    );

    await openDialog(tester, existing: existing);

    expect(find.widgetWithText(TextField, 'Zone 2 too humid'), findsOneWidget);
    expect(find.widgetWithText(TextField, '70.0'), findsOneWidget);
    expect(find.widgetWithText(TextField, '1440'), findsOneWidget);
  });
}
