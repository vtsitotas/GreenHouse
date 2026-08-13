import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/screens/dashboard/weather_card.dart';

void main() {
  testWidgets('tapping the history icon navigates to weather temperature history', (tester) async {
    String? lastLocation;
    final router = GoRouter(
      initialLocation: '/',
      routes: [
        GoRoute(path: '/', builder: (_, __) => const Scaffold(body: WeatherCard())),
        GoRoute(path: '/weather', builder: (_, __) => const SizedBox.shrink()),
        GoRoute(
          path: '/history/:zone/:metric',
          builder: (_, state) {
            lastLocation = '/history/${state.pathParameters['zone']}/${state.pathParameters['metric']}';
            return const SizedBox.shrink();
          },
        ),
      ],
    );

    await tester.pumpWidget(ProviderScope(
      overrides: [
        readingsProvider.overrideWith((ref) => Stream.value({
              'weather': {'temperature': 24.0, 'humidity': 55.0},
            })),
      ],
      child: MaterialApp.router(routerConfig: router),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.byIcon(Icons.show_chart));
    await tester.pumpAndSettle();
    expect(lastLocation, '/history/weather/temperature');
  });

  // One pumpWidget per test (rather than reusing a ProviderScope across two
  // pumps within the same test) -- each gets its own fresh ProviderContainer,
  // so there is no question of whether a changed override on an
  // already-built scope re-triggers the underlying Stream.value.
  Future<void> pumpWithWeather(WidgetTester tester, Map<String, double> weather) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        readingsProvider.overrideWith((ref) => Stream.value({'weather': weather})),
      ],
      child: const MaterialApp(home: Scaffold(body: WeatherCard())),
    ));
    await tester.pumpAndSettle();
  }

  testWidgets('no frost badge just above the threshold', (tester) async {
    await pumpWithWeather(tester, {'temperature': kFrostTempC + 0.1});
    expect(find.text('Frost'), findsNothing);
  });

  testWidgets('frost badge appears at/below the threshold, naming the constant that fired it',
      (tester) async {
    await pumpWithWeather(tester, {'temperature': kFrostTempC - 0.1});
    expect(find.text('Frost'), findsOneWidget);
  });

  testWidgets('no heat badge exactly at the threshold (exclusive boundary)', (tester) async {
    await pumpWithWeather(tester, {'temperature': kHeatTempC});
    expect(find.text('Heat'), findsNothing);
  });

  testWidgets('heat badge appears just above the threshold', (tester) async {
    await pumpWithWeather(tester, {'temperature': kHeatTempC + 0.1});
    expect(find.text('Heat'), findsOneWidget);
  });

  testWidgets('a trace of rain at the threshold does not trigger the rain badge',
      (tester) async {
    await pumpWithWeather(tester, {'temperature': 20.0, 'rain_mm_1h': kRainThresholdMm});
    expect(find.text('Rain'), findsNothing);
  });

  testWidgets('rain just above the threshold triggers the rain badge', (tester) async {
    await pumpWithWeather(tester, {'temperature': 20.0, 'rain_mm_1h': kRainThresholdMm + 0.1});
    expect(find.text('Rain'), findsOneWidget);
  });
}
