import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/providers/history_provider.dart';
import 'package:greenhouse_app/screens/history/history_screen.dart';

HistoryPoint _pt(int epochSeconds, double avg) => HistoryPoint(
      time: DateTime.fromMillisecondsSinceEpoch(epochSeconds * 1000),
      avg: avg,
      min: avg - 1,
      max: avg + 1,
    );

void main() {
  testWidgets('shows current value and default range for a zone metric', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        historyWithPredictionProvider.overrideWith((ref, query) async =>
            (actual: [_pt(0, 20.0), _pt(60, 22.0)], predicted: <HistoryPoint>[])),
      ],
      child: const MaterialApp(home: HistoryScreen(zone: 'zone1', metric: 'air_temperature')),
    ));
    await tester.pumpAndSettle();
    expect(
        tester.widget<Text>(find.byKey(const Key('current-value'))).data, '22.0°C');
    expect(find.text('Last 24 Hours'), findsOneWidget);
  });

  testWidgets('switching metric tab loads a different series', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        historyWithPredictionProvider.overrideWith((ref, query) async => (
              actual: query.metric == 'air_humidity' ? [_pt(0, 55.0)] : [_pt(0, 20.0)],
              predicted: <HistoryPoint>[],
            )),
      ],
      child: const MaterialApp(home: HistoryScreen(zone: 'zone1', metric: 'air_temperature')),
    ));
    await tester.pumpAndSettle();
    expect(
        tester.widget<Text>(find.byKey(const Key('current-value'))).data, '20.0°C');

    await tester.tap(find.text('Humidity'));
    await tester.pumpAndSettle();
    expect(
        tester.widget<Text>(find.byKey(const Key('current-value'))).data, '55.0%');
  });

  testWidgets('switching range chip loads a different window and label', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        historyWithPredictionProvider.overrideWith((ref, query) async => (
              actual: query.hours == 168 ? [_pt(0, 18.0)] : [_pt(0, 20.0)],
              predicted: <HistoryPoint>[],
            )),
      ],
      child: const MaterialApp(home: HistoryScreen(zone: 'zone1', metric: 'air_temperature')),
    ));
    await tester.pumpAndSettle();
    expect(find.text('Last 24 Hours'), findsOneWidget);

    await tester.tap(find.text('7d'));
    await tester.pumpAndSettle();
    expect(find.text('Last 7 Days'), findsOneWidget);
    expect(
        tester.widget<Text>(find.byKey(const Key('current-value'))).data, '18.0°C');
  });

  testWidgets('shows empty-state message when there is no data', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        historyWithPredictionProvider.overrideWith(
            (ref, query) async => (actual: <HistoryPoint>[], predicted: <HistoryPoint>[])),
      ],
      child: const MaterialApp(home: HistoryScreen(zone: 'zone1', metric: 'air_temperature')),
    ));
    await tester.pumpAndSettle();
    expect(find.textContaining('No history yet'), findsOneWidget);
  });

  testWidgets('shows error message when the fetch fails', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        historyWithPredictionProvider.overrideWith((ref, query) async => throw Exception('boom')),
      ],
      child: const MaterialApp(home: HistoryScreen(zone: 'zone1', metric: 'air_temperature')),
    ));
    await tester.pumpAndSettle();
    expect(find.textContaining('Could not load history'), findsOneWidget);
  });

  testWidgets('weather sentinel zone shows weather metric tabs', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        historyWithPredictionProvider.overrideWith((ref, query) async =>
            (actual: [_pt(0, 20.0), _pt(60, 21.0)], predicted: <HistoryPoint>[])),
      ],
      child: const MaterialApp(home: HistoryScreen(zone: 'weather', metric: 'temperature')),
    ));
    await tester.pumpAndSettle();
    expect(find.text('Wind'), findsOneWidget);
    expect(find.text('Rain'), findsOneWidget);
  });

  testWidgets('Custom chip opens a date-range picker and switches to a since/until query',
      (tester) async {
    HistoryQuery? lastQuery;
    await tester.pumpWidget(ProviderScope(
      overrides: [
        historyWithPredictionProvider.overrideWith((ref, query) async {
          lastQuery = query;
          return (actual: [_pt(0, 20.0), _pt(60, 21.0)], predicted: <HistoryPoint>[]);
        }),
      ],
      child: const MaterialApp(home: HistoryScreen(zone: 'zone1', metric: 'air_temperature')),
    ));
    await tester.pumpAndSettle();
    expect(lastQuery!.isCustomRange, isFalse);
    expect(find.text('Last 24 Hours'), findsOneWidget);

    await tester.tap(find.text('Custom…'));
    await tester.pumpAndSettle();
    // The range picker opens pre-populated with today→today (see Step 3),
    // so tapping Save immediately confirms a valid single-day range without
    // needing to navigate the calendar grid.
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();

    expect(lastQuery!.isCustomRange, isTrue);
    expect(find.text('Last 24 Hours'), findsNothing);
    expect(find.text('now'), findsNothing); // header no longer claims a past date is "now"
  });
}
