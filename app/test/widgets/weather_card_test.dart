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
}
