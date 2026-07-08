# History Chart Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the zone/weather history chart screen with real axes, gridlines, a time-range selector, metric switching, a min–max band, and a short prediction overlay (weather forecast for temp/rain, trend extrapolation otherwise).

**Architecture:** Replace the hand-rolled `CustomPainter` in `HistoryScreen` with `fl_chart`'s `LineChart`. Extend `HistoryQuery`/`historyPointsProvider` to carry `kind` and `hours`; add a new `historyWithPredictionProvider` that layers a prediction series (trend regression or forecast overlay) on top of the real data. Wire new tap targets from `ZoneCard` (per-metric chips) and `WeatherCard` (a history icon) into the existing `/history/:zone/:metric` route, reusing `zone == 'weather'` as the sentinel for weather-kind queries — no router changes needed.

**Tech Stack:** Flutter 3.32.4 / Dart 3.8.1, Riverpod 2.x, go_router, `fl_chart` (new dependency), `mocktail` + `flutter_test` for tests.

**Reference spec:** `docs/superpowers/specs/2026-07-08-history-chart-enhancement-design.md`

## Global Constraints

- No changes to any file under `pi/` — the backend already supports everything needed (`kind`, `zone`, `metric`, `hours` params on `/api/history`).
- No new dependency beyond `fl_chart`. Do not add `intl` — date/time formatting stays manual (matches existing `history_screen.dart` convention).
- Weather metric tabs are exactly: Temp (`temperature`), Humidity (`humidity`), Wind (`wind_kmh`), UV (`uv_index`), Rain (`rain_mm_1h`). No Pressure tab — the recorder never stores `pressure` (see spec's Non-goals).
- Forecast-overlay prediction applies only to `temperature` and `rain_mm_1h`. Every other metric uses trend extrapolation. Both always fall back gracefully — a chart must never error or hang because of missing/slow forecast data.
- Follow existing test conventions in `app/test/`: `mocktail` for mocks, `ProviderScope`/`ProviderContainer` overrides for Riverpod, no golden-image tests.

---

### Task 1: `HistoryQuery` gains `kind`/`hours`, `zone` becomes nullable

**Files:**
- Modify: `app/lib/providers/history_provider.dart`
- Test: `app/test/providers/history_provider_test.dart` (new)

**Interfaces:**
- Produces: `HistoryQuery({String? zone, String? kind, required String metric, double hours = 24})` — `kind` defaults to `'zone'` when `zone != null`, else `'weather'`. `==`/`hashCode` cover all four fields.
- Produces: `historyPointsProvider` (unchanged name/shape: `FutureProvider.family<List<HistoryPoint>, HistoryQuery>`), now forwarding `query.kind` and `query.hours` to `HistoryService.fetchPoints` (which already accepts both — see `app/lib/services/history_service.dart`).
- Consumes: `HistoryService.fetchPoints({required lanHost, zone, kind, required metric, hours})` (already exists, unchanged).

- [ ] **Step 1: Write the failing tests**

Create `app/test/providers/history_provider_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/providers/history_provider.dart';
import 'package:greenhouse_app/services/history_service.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

class MockHistoryService extends Mock implements HistoryService {}

class MockPairingService extends Mock implements PairingService {}

void main() {
  test('HistoryQuery infers kind from zone when not given explicitly', () {
    expect(const HistoryQuery(zone: 'zone1', metric: 'air_temperature').kind, 'zone');
    expect(const HistoryQuery(zone: null, metric: 'temperature').kind, 'weather');
  });

  test('HistoryQuery equality considers zone, kind, metric, and hours', () {
    const a = HistoryQuery(zone: 'zone1', metric: 'air_temperature', hours: 24);
    const b = HistoryQuery(zone: 'zone1', metric: 'air_temperature', hours: 24);
    const c = HistoryQuery(zone: 'zone1', metric: 'air_temperature', hours: 168);
    expect(a, b);
    expect(a == c, isFalse);
  });

  test('historyPointsProvider passes kind/hours/zone through to HistoryService', () async {
    final mockService = MockHistoryService();
    final mockPairing = MockPairingService();
    when(() => mockPairing.loadConfig()).thenAnswer((_) async => const ConnectionConfig(
          lanHost: 'greenhouse.local',
          remoteHost: '',
          port: 8883,
          tlsFingerprint: '',
          username: '',
          password: '',
          remoteUsername: '',
          remotePassword: '',
        ));
    when(() => mockService.fetchPoints(
          lanHost: any(named: 'lanHost'),
          zone: any(named: 'zone'),
          kind: any(named: 'kind'),
          metric: any(named: 'metric'),
          hours: any(named: 'hours'),
        )).thenAnswer((_) async => [
          HistoryPoint(time: DateTime.fromMillisecondsSinceEpoch(0), avg: 1, min: 1, max: 1),
        ]);

    final container = ProviderContainer(overrides: [
      historyServiceProvider.overrideWithValue(mockService),
      pairingServiceProvider.overrideWithValue(mockPairing),
    ]);
    addTearDown(container.dispose);

    const query = HistoryQuery(zone: null, kind: 'weather', metric: 'temperature', hours: 168);
    final result = await container.read(historyPointsProvider(query).future);

    expect(result.length, 1);
    verify(() => mockService.fetchPoints(
          lanHost: 'greenhouse.local',
          zone: null,
          kind: 'weather',
          metric: 'temperature',
          hours: 168,
        )).called(1);
  });
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/providers/history_provider_test.dart`
Expected: FAIL — `HistoryQuery` has no `kind` field yet, and the named-parameter shapes don't match.

- [ ] **Step 3: Update `history_provider.dart`**

Replace the full contents of `app/lib/providers/history_provider.dart`:

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/services/history_service.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

final historyServiceProvider = Provider((_) => HistoryService());

class HistoryQuery {
  final String? zone;
  final String kind;
  final String metric;
  final double hours;

  const HistoryQuery({
    this.zone,
    String? kind,
    required this.metric,
    this.hours = 24,
  }) : kind = kind ?? (zone != null ? 'zone' : 'weather');

  @override
  bool operator ==(Object other) =>
      other is HistoryQuery &&
      other.zone == zone &&
      other.kind == kind &&
      other.metric == metric &&
      other.hours == hours;

  @override
  int get hashCode => Object.hash(zone, kind, metric, hours);
}

final historyPointsProvider =
    FutureProvider.family<List<HistoryPoint>, HistoryQuery>((ref, query) async {
  final config = await ref.read(pairingServiceProvider).loadConfig();
  if (config == null) return [];
  final service = ref.read(historyServiceProvider);
  return service.fetchPoints(
    lanHost: config.lanHost,
    zone: query.zone,
    kind: query.kind,
    metric: query.metric,
    hours: query.hours,
  );
});
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/providers/history_provider_test.dart`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/lib/providers/history_provider.dart app/test/providers/history_provider_test.dart
git commit -m "feat: add kind/hours to HistoryQuery, nullable zone for weather metrics"
```

---

### Task 2: `predictTrend()` — linear-regression extrapolation

**Files:**
- Create: `app/lib/utils/history_prediction.dart`
- Test: `app/test/utils/history_prediction_test.dart` (new)

**Interfaces:**
- Produces: `List<HistoryPoint> predictTrend(List<HistoryPoint> recent, {required int stepSeconds, required int futureSteps, double? clampMin})`.
- Produces: `double? clampFloorFor(String metric)` — returns `0.0` for `{'soil_moisture', 'air_humidity', 'humidity', 'uv_index', 'light_lux'}`, else `null`.
- Consumes: `HistoryPoint` (`time`, `avg`, `min`, `max`) from `app/lib/models/history_point.dart`.

- [ ] **Step 1: Write the failing tests**

Create `app/test/utils/history_prediction_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/utils/history_prediction.dart';

HistoryPoint _pt(int epochSeconds, double avg) => HistoryPoint(
      time: DateTime.fromMillisecondsSinceEpoch(epochSeconds * 1000),
      avg: avg,
      min: avg,
      max: avg,
    );

void main() {
  group('predictTrend', () {
    test('extrapolates a perfectly linear increasing series', () {
      final recent = [_pt(0, 10), _pt(60, 12), _pt(120, 14), _pt(180, 16)];
      final result = predictTrend(recent, stepSeconds: 60, futureSteps: 2);
      expect(result.length, 2);
      expect(result[0].avg, closeTo(18.0, 0.01));
      expect(result[1].avg, closeTo(20.0, 0.01));
      expect(result[0].time, DateTime.fromMillisecondsSinceEpoch(240000));
      expect(result[1].time, DateTime.fromMillisecondsSinceEpoch(300000));
    });

    test('extrapolates a flat series as flat', () {
      final recent = [_pt(0, 30), _pt(60, 30), _pt(120, 30)];
      final result = predictTrend(recent, stepSeconds: 60, futureSteps: 2);
      expect(result[0].avg, closeTo(30.0, 0.01));
      expect(result[1].avg, closeTo(30.0, 0.01));
    });

    test('clamps extrapolated values at the given floor', () {
      final recent = [_pt(0, 10), _pt(60, 5), _pt(120, 0)];
      final result = predictTrend(recent, stepSeconds: 60, futureSteps: 3, clampMin: 0.0);
      expect(result.every((p) => p.avg >= 0), isTrue);
    });

    test('returns empty list with fewer than 2 points', () {
      expect(predictTrend([_pt(0, 10)], stepSeconds: 60, futureSteps: 3), isEmpty);
      expect(predictTrend([], stepSeconds: 60, futureSteps: 3), isEmpty);
    });

    test('returns empty list when futureSteps is 0', () {
      final recent = [_pt(0, 10), _pt(60, 12)];
      expect(predictTrend(recent, stepSeconds: 60, futureSteps: 0), isEmpty);
    });
  });

  group('clampFloorFor', () {
    test('returns 0.0 for percent-like metrics', () {
      expect(clampFloorFor('soil_moisture'), 0.0);
      expect(clampFloorFor('humidity'), 0.0);
      expect(clampFloorFor('uv_index'), 0.0);
    });

    test('returns null for metrics that can legitimately go negative', () {
      expect(clampFloorFor('air_temperature'), isNull);
      expect(clampFloorFor('temperature'), isNull);
    });
  });
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/utils/history_prediction_test.dart`
Expected: FAIL — `package:greenhouse_app/utils/history_prediction.dart` does not exist.

- [ ] **Step 3: Implement `history_prediction.dart`**

Create `app/lib/utils/history_prediction.dart`:

```dart
import 'package:greenhouse_app/models/history_point.dart';

const _zeroFloorMetrics = {
  'soil_moisture',
  'air_humidity',
  'humidity',
  'uv_index',
  'light_lux',
};

/// Floor to clamp extrapolated values at, or null if the metric can
/// legitimately go negative (e.g. temperature).
double? clampFloorFor(String metric) => _zeroFloorMetrics.contains(metric) ? 0.0 : null;

/// Extrapolates a short trend forward from the tail of [recent] using
/// ordinary least-squares linear regression against real elapsed time.
/// Returns an empty list when there are fewer than 2 points to fit, or
/// when [futureSteps] is 0.
List<HistoryPoint> predictTrend(
  List<HistoryPoint> recent, {
  required int stepSeconds,
  required int futureSteps,
  double? clampMin,
}) {
  if (recent.length < 2 || futureSteps < 1) return [];
  final n = recent.length;
  final xs = recent.map((p) => p.time.millisecondsSinceEpoch / 1000.0).toList();
  final ys = recent.map((p) => p.avg).toList();
  final xMean = xs.reduce((a, b) => a + b) / n;
  final yMean = ys.reduce((a, b) => a + b) / n;

  var num = 0.0;
  var den = 0.0;
  for (var i = 0; i < n; i++) {
    num += (xs[i] - xMean) * (ys[i] - yMean);
    den += (xs[i] - xMean) * (xs[i] - xMean);
  }
  final slope = den == 0 ? 0.0 : num / den;
  final intercept = yMean - slope * xMean;

  final lastTime = recent.last.time;
  final out = <HistoryPoint>[];
  for (var i = 1; i <= futureSteps; i++) {
    final t = lastTime.add(Duration(seconds: stepSeconds * i));
    var value = slope * (t.millisecondsSinceEpoch / 1000.0) + intercept;
    if (clampMin != null && value < clampMin) value = clampMin;
    out.add(HistoryPoint(time: t, avg: value, min: value, max: value));
  }
  return out;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/utils/history_prediction_test.dart`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/lib/utils/history_prediction.dart app/test/utils/history_prediction_test.dart
git commit -m "feat: add predictTrend linear-regression extrapolation helper"
```

---

### Task 3: `predictFromForecast()` — weather-forecast overlay mapper

**Files:**
- Modify: `app/lib/utils/history_prediction.dart`
- Test: `app/test/utils/history_prediction_test.dart`

**Interfaces:**
- Produces: `List<HistoryPoint> predictFromForecast({required Map<String, dynamic> forecast, required String metric, required DateTime after, required DateTime until})`. `metric` must be `'temperature'` or `'rain_mm_1h'`; any other value returns `[]`. Reads `forecast['times']` (ISO8601 strings) and `forecast['temps']` or `forecast['precip']` depending on `metric` (matches the payload shape published by `pi/scripts/weather.py`'s `greenhouse/weather/forecast` topic).
- Consumes: nothing new — same `HistoryPoint` model.

- [ ] **Step 1: Write the failing tests**

Append to `app/test/utils/history_prediction_test.dart` (inside `main()`, alongside the existing groups):

```dart
  group('predictFromForecast', () {
    final forecast = {
      'times': [
        '2026-07-08T10:00:00',
        '2026-07-08T11:00:00',
        '2026-07-08T12:00:00',
      ],
      'temps': [20.0, 21.0, 22.0],
      'precip': [0.0, 0.5, 1.0],
    };

    test('maps temperature forecast within the (after, until] window', () {
      final result = predictFromForecast(
        forecast: forecast,
        metric: 'temperature',
        after: DateTime.parse('2026-07-08T10:00:00'),
        until: DateTime.parse('2026-07-08T12:00:00'),
      );
      expect(result.length, 2);
      expect(result[0].avg, 21.0);
      expect(result[1].avg, 22.0);
    });

    test('maps rain forecast using the precip array', () {
      final result = predictFromForecast(
        forecast: forecast,
        metric: 'rain_mm_1h',
        after: DateTime.parse('2026-07-08T10:00:00'),
        until: DateTime.parse('2026-07-08T12:00:00'),
      );
      expect(result.map((p) => p.avg), [0.5, 1.0]);
    });

    test('returns empty list for a metric with no forecast series', () {
      final result = predictFromForecast(
        forecast: forecast,
        metric: 'wind_kmh',
        after: DateTime.parse('2026-07-08T10:00:00'),
        until: DateTime.parse('2026-07-08T12:00:00'),
      );
      expect(result, isEmpty);
    });

    test('skips malformed timestamps instead of throwing', () {
      final broken = {
        'times': ['not-a-date', '2026-07-08T11:00:00'],
        'temps': [99.0, 21.0],
      };
      final result = predictFromForecast(
        forecast: broken,
        metric: 'temperature',
        after: DateTime.parse('2026-07-08T10:00:00'),
        until: DateTime.parse('2026-07-08T12:00:00'),
      );
      expect(result.length, 1);
      expect(result[0].avg, 21.0);
    });
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/utils/history_prediction_test.dart`
Expected: FAIL — `predictFromForecast` is not defined.

- [ ] **Step 3: Implement `predictFromForecast`**

Append to `app/lib/utils/history_prediction.dart`:

```dart

/// Maps the app-wide weather forecast payload (times/temps/precip arrays,
/// as published on `greenhouse/weather/forecast`) into HistoryPoint-shaped
/// predictions for [metric], restricted to the (after, until] window.
/// [metric] must be 'temperature' or 'rain_mm_1h'; anything else returns [].
List<HistoryPoint> predictFromForecast({
  required Map<String, dynamic> forecast,
  required String metric,
  required DateTime after,
  required DateTime until,
}) {
  final key = switch (metric) {
    'temperature' => 'temps',
    'rain_mm_1h' => 'precip',
    _ => null,
  };
  if (key == null) return [];

  final times = (forecast['times'] as List?) ?? const [];
  final values = (forecast[key] as List?) ?? const [];
  final n = times.length < values.length ? times.length : values.length;

  final out = <HistoryPoint>[];
  for (var i = 0; i < n; i++) {
    DateTime t;
    try {
      t = DateTime.parse(times[i] as String);
    } catch (_) {
      continue;
    }
    if (t.isAfter(after) && !t.isAfter(until)) {
      final v = (values[i] as num).toDouble();
      out.add(HistoryPoint(time: t, avg: v, min: v, max: v));
    }
  }
  return out;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/utils/history_prediction_test.dart`
Expected: PASS (11 tests total)

- [ ] **Step 5: Commit**

```bash
git add app/lib/utils/history_prediction.dart app/test/utils/history_prediction_test.dart
git commit -m "feat: add predictFromForecast weather-forecast overlay mapper"
```

---

### Task 4: `historyWithPredictionProvider` — combine real + predicted data

**Files:**
- Modify: `app/lib/providers/history_provider.dart`
- Test: `app/test/providers/history_provider_test.dart`

**Interfaces:**
- Produces: `typedef HistoryData = ({List<HistoryPoint> actual, List<HistoryPoint> predicted})` and `final historyWithPredictionProvider = FutureProvider.family<HistoryData, HistoryQuery>(...)`.
- Consumes: `historyPointsProvider` (Task 1), `predictTrend`/`predictFromForecast`/`clampFloorFor` (Tasks 2–3), `forecastProvider` from `app/lib/providers/connection_provider.dart` (existing — `StreamProvider<Map<String, dynamic>>`).

- [ ] **Step 1: Write the failing tests**

Append to `app/test/providers/history_provider_test.dart` (add these imports at the top: `import 'package:greenhouse_app/providers/connection_provider.dart';`) and add this group inside `main()`:

```dart
  group('historyWithPredictionProvider', () {
    HistoryPoint pt(int epochSeconds, double avg) => HistoryPoint(
          time: DateTime.fromMillisecondsSinceEpoch(epochSeconds * 1000),
          avg: avg,
          min: avg,
          max: avg,
        );

    test('returns no prediction when fewer than 2 actual points', () async {
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20)]),
      ]);
      addTearDown(container.dispose);
      const query = HistoryQuery(zone: 'zone1', metric: 'air_temperature', hours: 24);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.actual.length, 1);
      expect(data.predicted, isEmpty);
    });

    test('uses forecast overlay for weather temperature when available', () async {
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20), pt(60, 21)]),
        forecastProvider.overrideWith((ref) => Stream.value({
              'times': [DateTime.fromMillisecondsSinceEpoch(120000).toUtc().toIso8601String()],
              'temps': [25.0],
              'precip': [0.0],
            })),
      ]);
      addTearDown(container.dispose);
      const query = HistoryQuery(zone: null, kind: 'weather', metric: 'temperature', hours: 24);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.predicted, isNotEmpty);
      expect(data.predicted.first.avg, 25.0);
    });

    test('falls back to trend extrapolation for zone metrics (no forecast)', () async {
      final container = ProviderContainer(overrides: [
        historyPointsProvider.overrideWith((ref, query) async => [pt(0, 20), pt(60, 22)]),
      ]);
      addTearDown(container.dispose);
      const query = HistoryQuery(zone: 'zone1', metric: 'soil_moisture', hours: 24);
      final data = await container.read(historyWithPredictionProvider(query).future);
      expect(data.predicted, isNotEmpty);
      expect(data.predicted.first.avg, greaterThan(22.0));
    });
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/providers/history_provider_test.dart`
Expected: FAIL — `historyWithPredictionProvider` is not defined.

- [ ] **Step 3: Add `historyWithPredictionProvider`**

Append to `app/lib/providers/history_provider.dart` (add these two imports at the top alongside the existing ones: `import 'package:greenhouse_app/providers/connection_provider.dart';` and `import 'package:greenhouse_app/utils/history_prediction.dart';`):

```dart

typedef HistoryData = ({List<HistoryPoint> actual, List<HistoryPoint> predicted});

final historyWithPredictionProvider =
    FutureProvider.family<HistoryData, HistoryQuery>((ref, query) async {
  final actual = await ref.watch(historyPointsProvider(query).future);
  if (actual.length < 2) {
    return (actual: actual, predicted: <HistoryPoint>[]);
  }

  final stepSeconds = query.hours <= 48 ? 60 : 3600;
  final futureSteps = ((query.hours * 3600 * 0.2) / stepSeconds).round().clamp(1, 100);
  final recentWindow = actual.length > 12 ? actual.sublist(actual.length - 12) : actual;
  final clampMin = clampFloorFor(query.metric);

  final canForecast = query.kind == 'weather' &&
      (query.metric == 'temperature' || query.metric == 'rain_mm_1h');

  List<HistoryPoint> predicted = const [];
  if (canForecast) {
    try {
      final forecast =
          await ref.watch(forecastProvider.future).timeout(const Duration(seconds: 3));
      final after = actual.last.time;
      final until = after.add(Duration(seconds: stepSeconds * futureSteps));
      predicted = predictFromForecast(
          forecast: forecast, metric: query.metric, after: after, until: until);
    } catch (_) {
      predicted = const [];
    }
  }
  if (predicted.isEmpty) {
    predicted = predictTrend(recentWindow,
        stepSeconds: stepSeconds, futureSteps: futureSteps, clampMin: clampMin);
  }
  return (actual: actual, predicted: predicted);
});
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/providers/history_provider_test.dart`
Expected: PASS (6 tests total)

- [ ] **Step 5: Commit**

```bash
git add app/lib/providers/history_provider.dart app/test/providers/history_provider_test.dart
git commit -m "feat: add historyWithPredictionProvider combining real + predicted data"
```

---

### Task 5: `ZoneCard` — per-metric-chip navigation

**Files:**
- Modify: `app/lib/screens/dashboard/zone_card.dart`
- Test: `app/test/widgets/zone_card_test.dart`

**Interfaces:**
- Produces: `ZoneCard` no longer wraps the whole card in a single tap target; each metric `_Chip` (Temp/Humidity/Soil/Light) is independently tappable, routing to `/history/{zone}/{metric}` for its own metric. The Pressure chip stays non-interactive (recorder doesn't track it — see Global Constraints).

- [ ] **Step 1: Write the failing tests**

Append to `app/test/widgets/zone_card_test.dart` (add `import 'package:flutter_riverpod/flutter_riverpod.dart';` is not needed; add `import 'package:go_router/go_router.dart';` at the top):

```dart

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && flutter test test/widgets/zone_card_test.dart`
Expected: FAIL — no widget with `Key('chip_air_temperature')` exists yet.

- [ ] **Step 3: Rewrite `zone_card.dart`**

Replace the full contents of `app/lib/screens/dashboard/zone_card.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class ZoneCard extends StatelessWidget {
  final String zone;
  final Map<String, double> readings;
  const ZoneCard({required this.zone, required this.readings, super.key});

  String get _title {
    if (zone.startsWith('zone')) return 'Zone ${zone.substring(4)}';
    return '${zone[0].toUpperCase()}${zone.substring(1)}';
  }

  @override
  Widget build(BuildContext context) {
    final soil = readings['soil/moisture'];
    final lowSoil = soil != null && soil < 30;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(_title, style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)),
            if (lowSoil) ...[
              const SizedBox(width: 8),
              const Icon(Icons.warning_amber_rounded, color: AppColors.warning, size: 18),
            ],
          ]),
          const SizedBox(height: 12),
          Wrap(spacing: 16, runSpacing: 8, children: [
            if (readings['air/temperature'] != null)
              _Chip(
                key: const Key('chip_air_temperature'),
                icon: Icons.thermostat,
                label: 'Temp',
                value: '${readings['air/temperature']!.toStringAsFixed(1)} °C',
                onTap: () => context.push('/history/$zone/air_temperature'),
              ),
            if (readings['air/humidity'] != null)
              _Chip(
                key: const Key('chip_air_humidity'),
                icon: Icons.water_drop,
                label: 'Humidity',
                value: '${readings['air/humidity']!.toStringAsFixed(0)} %',
                onTap: () => context.push('/history/$zone/air_humidity'),
              ),
            if (soil != null)
              _Chip(
                key: const Key('chip_soil_moisture'),
                icon: Icons.grass,
                label: 'Soil',
                value: '${soil.toStringAsFixed(0)} %',
                color: lowSoil ? AppColors.warning : null,
                onTap: () => context.push('/history/$zone/soil_moisture'),
              ),
            if (readings['light/lux'] != null)
              _Chip(
                key: const Key('chip_light_lux'),
                icon: Icons.wb_sunny,
                label: 'Light',
                value: '${readings['light/lux']!.toStringAsFixed(0)} lux',
                onTap: () => context.push('/history/$zone/light_lux'),
              ),
            if (readings['pressure'] != null)
              _Chip(
                icon: Icons.speed,
                label: 'Pressure',
                value: '${readings['pressure']!.toStringAsFixed(0)} hPa',
              ),
          ]),
        ]),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final Color? color;
  final VoidCallback? onTap;
  const _Chip({
    super.key,
    required this.icon,
    required this.label,
    required this.value,
    this.color,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final content = Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(icon, size: 16, color: color ?? Theme.of(context).colorScheme.primary),
      const SizedBox(width: 4),
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: Theme.of(context).textTheme.labelSmall),
        Text(value, style: TextStyle(fontWeight: FontWeight.w600, color: color)),
      ]),
    ]);
    if (onTap == null) return content;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
        child: content,
      ),
    );
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && flutter test test/widgets/zone_card_test.dart`
Expected: PASS (5 tests total — the 3 pre-existing tests plus the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add app/lib/screens/dashboard/zone_card.dart app/test/widgets/zone_card_test.dart
git commit -m "feat: per-metric-chip navigation on ZoneCard"
```

---

### Task 6: `WeatherCard` — add weather-history entry point

**Files:**
- Modify: `app/lib/screens/dashboard/weather_card.dart`
- Test: `app/test/widgets/weather_card_test.dart` (new)

**Interfaces:**
- Produces: `WeatherCard` gains an `IconButton` (`Icons.show_chart`) in its header row that routes to `/history/weather/temperature` (the existing `/history/:zone/:metric` route, with `zone == 'weather'` as the sentinel `HistoryScreen` already understands after Task 7). The rest of the card's existing tap-to-`/weather` behavior is unchanged.

- [ ] **Step 1: Write the failing test**

Create `app/test/widgets/weather_card_test.dart`:

```dart
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && flutter test test/widgets/weather_card_test.dart`
Expected: FAIL — no `Icons.show_chart` widget exists yet.

- [ ] **Step 3: Add the history icon button**

In `app/lib/screens/dashboard/weather_card.dart`, locate this block (inside the header `Row`):

```dart
                const Spacer(),
                if (isFrost)
                  const _AlertBadge('Frost', Colors.lightBlue),
                if (hasRain && !isFrost)
                  const _AlertBadge('Rain', Colors.lightBlue),
                if (isHot)
                  const _AlertBadge('Heat', Colors.orange),
                const SizedBox(width: 4),
                const Icon(Icons.arrow_forward_ios, color: Colors.white54, size: 14),
```

Replace it with:

```dart
                const Spacer(),
                if (isFrost)
                  const _AlertBadge('Frost', Colors.lightBlue),
                if (hasRain && !isFrost)
                  const _AlertBadge('Rain', Colors.lightBlue),
                if (isHot)
                  const _AlertBadge('Heat', Colors.orange),
                IconButton(
                  icon: const Icon(Icons.show_chart, color: Colors.white70, size: 18),
                  tooltip: 'View history',
                  onPressed: () => context.push('/history/weather/temperature'),
                ),
                const Icon(Icons.arrow_forward_ios, color: Colors.white54, size: 14),
```

No import changes needed — `weather_card.dart` already imports `package:go_router/go_router.dart` (it's used by the existing `context.go('/weather')` call), so `context.push` is immediately available.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && flutter test test/widgets/weather_card_test.dart`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lib/screens/dashboard/weather_card.dart app/test/widgets/weather_card_test.dart
git commit -m "feat: add weather-history entry point to WeatherCard"
```

---

### Task 7: Rebuild `HistoryScreen` with `fl_chart` — axes, grid, tabs, range selector, prediction overlay

**Files:**
- Modify: `app/pubspec.yaml`
- Modify: `app/lib/screens/history/history_screen.dart` (full rewrite)
- Test: `app/test/widgets/history_screen_test.dart` (new)

**Interfaces:**
- Consumes: `historyWithPredictionProvider` (Task 4), `HistoryQuery` (Task 1), `HistoryPoint` (existing model).
- Produces: `HistoryScreen({required String zone, required String metric})` — same public constructor signature as before (so `app.dart`'s existing route needs no change). Internally, `zone == 'weather'` means a weather-kind query with no zone; any other value is a real zone.

- [ ] **Step 1: Add the `fl_chart` dependency**

In `app/pubspec.yaml`, add this line under `dependencies:` (alongside the existing entries, e.g. right after `geolocator: ^13.0.2`):

```yaml
  fl_chart: ^1.1.0
```

(Verified via `dart pub add fl_chart --dry-run` against this project's exact SDK constraints on 2026-07-08 — it resolves to `1.1.0`, pulling in `equatable 2.1.0` as a transitive dependency. Use whatever `flutter pub get` actually resolves to; don't fight it back down to a hypothetical older version.)

Run: `cd app && flutter pub get`
Expected: resolves successfully, `fl_chart` and `equatable` appear in `pubspec.lock`.

**Note on API drift:** the `_HistoryChart` code in Step 4 below is written against fl_chart's well-established `LineChartData`/`LineChartBarData`/`FlSpot`/`BetweenBarsData`/`AxisTitles`/`SideTitles`/`LineTouchData`/`LineTooltipItem` API shape. This has been stable across fl_chart's 0.x → 1.x transition in the general case, but if `flutter analyze` (Task 8) reports a mismatch against the exact installed `1.1.0` API (e.g. a renamed field), fix it by checking the actual class definition under `~/.pub-cache/hosted/pub.dev/fl_chart-1.1.0/lib/src/chart/line_chart/` rather than guessing — the intent of each property (documented inline above each usage) stays the same even if a name shifts.

- [ ] **Step 2: Write the failing widget tests**

Create `app/test/widgets/history_screen_test.dart`:

```dart
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
    expect(find.text('22.0°C'), findsOneWidget);
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
    expect(find.text('20.0°C'), findsOneWidget);

    await tester.tap(find.text('Humidity'));
    await tester.pumpAndSettle();
    expect(find.text('55.0%'), findsOneWidget);
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
    expect(find.text('18.0°C'), findsOneWidget);
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
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd app && flutter test test/widgets/history_screen_test.dart`
Expected: FAIL — `HistoryScreen` doesn't yet support tabs, range chips, or the `HistoryData` shape.

- [ ] **Step 4: Rewrite `history_screen.dart`**

Replace the full contents of `app/lib/screens/history/history_screen.dart`:

```dart
import 'dart:math' as math;
import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/providers/history_provider.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

String _unitFor(String metric) {
  switch (metric) {
    case 'air_temperature':
    case 'temperature':
      return '°C';
    case 'air_humidity':
    case 'humidity':
      return '%';
    case 'soil_moisture':
      return '%';
    case 'light_lux':
      return 'lux';
    case 'wind_kmh':
      return 'km/h';
    case 'uv_index':
      return '';
    case 'rain_mm_1h':
      return 'mm';
    default:
      return '';
  }
}

const _weekdayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _monthNames = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
];
String _twoDigits(int n) => n.toString().padLeft(2, '0');
String _timeLabel(DateTime t) => '${_twoDigits(t.hour)}:${_twoDigits(t.minute)}';

const _zoneMetrics = ['air_temperature', 'air_humidity', 'soil_moisture', 'light_lux'];
const _zoneLabels = {
  'air_temperature': 'Temp',
  'air_humidity': 'Humidity',
  'soil_moisture': 'Soil',
  'light_lux': 'Light',
};
const _weatherMetrics = ['temperature', 'humidity', 'wind_kmh', 'uv_index', 'rain_mm_1h'];
const _weatherLabels = {
  'temperature': 'Temp',
  'humidity': 'Humidity',
  'wind_kmh': 'Wind',
  'uv_index': 'UV',
  'rain_mm_1h': 'Rain',
};
const _ranges = [(24.0, '24h'), (168.0, '7d'), (720.0, '30d'), (2160.0, '90d')];

class HistoryScreen extends ConsumerStatefulWidget {
  final String zone;
  final String metric;
  const HistoryScreen({required this.zone, required this.metric, super.key});

  @override
  ConsumerState<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends ConsumerState<HistoryScreen> {
  late final String _kind;
  late final String? _zone;
  late String _metric;
  double _hours = 24;

  @override
  void initState() {
    super.initState();
    _kind = widget.zone == 'weather' ? 'weather' : 'zone';
    _zone = _kind == 'weather' ? null : widget.zone;
    _metric = widget.metric;
  }

  List<String> get _availableMetrics => _kind == 'weather' ? _weatherMetrics : _zoneMetrics;
  String _labelFor(String m) => (_kind == 'weather' ? _weatherLabels[m] : _zoneLabels[m]) ?? m;

  String get _title {
    if (_kind == 'weather') return 'Weather — ${_labelFor(_metric)}';
    final z = _zone!;
    final zoneLabel = z.startsWith('zone') ? 'Zone ${z.substring(4)}' : z;
    return '$zoneLabel — ${_labelFor(_metric)}';
  }

  String get _rangeLabel {
    final tag = _ranges.firstWhere((r) => r.$1 == _hours, orElse: () => (_hours, '')).$2;
    return switch (tag) {
      '24h' => 'Last 24 Hours',
      '7d' => 'Last 7 Days',
      '30d' => 'Last 30 Days',
      '90d' => 'Last 90 Days',
      _ => 'Last ${_hours.round()} Hours',
    };
  }

  String _axisTimeLabel(DateTime t) {
    if (_hours <= 24) return _timeLabel(t);
    if (_hours <= 168) return '${_weekdayNames[t.weekday - 1]} ${_timeLabel(t)}';
    return '${_monthNames[t.month - 1]} ${t.day}';
  }

  @override
  Widget build(BuildContext context) {
    final query = HistoryQuery(zone: _zone, kind: _kind, metric: _metric, hours: _hours);
    final dataAsync = ref.watch(historyWithPredictionProvider(query));
    final unit = _unitFor(_metric);

    return Scaffold(
      appBar: AppBar(title: Text(_title)),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: Wrap(
              spacing: 8,
              children: _availableMetrics
                  .map((m) => ChoiceChip(
                        label: Text(_labelFor(m)),
                        selected: m == _metric,
                        onSelected: (_) => setState(() => _metric = m),
                      ))
                  .toList(),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Wrap(
              spacing: 8,
              children: _ranges
                  .map((r) => ChoiceChip(
                        label: Text(r.$2),
                        selected: r.$1 == _hours,
                        onSelected: (_) => setState(() => _hours = r.$1),
                      ))
                  .toList(),
            ),
          ),
          Expanded(
            child: dataAsync.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => Center(
                child: Padding(
                  padding: const EdgeInsets.all(32),
                  child: Text('Could not load history.\n$e', textAlign: TextAlign.center),
                ),
              ),
              data: (data) {
                final actual = data.actual;
                if (actual.isEmpty) {
                  return Center(
                    child: Text(
                      'No history yet for this metric in the ${_rangeLabel.toLowerCase()}.',
                      textAlign: TextAlign.center,
                    ),
                  );
                }
                final latest = actual.last;
                final minV = actual.map((p) => p.min).reduce(math.min);
                final maxV = actual.map((p) => p.max).reduce(math.max);
                return Padding(
                  padding: const EdgeInsets.all(16),
                  child: Card(
                    elevation: 4,
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            crossAxisAlignment: CrossAxisAlignment.baseline,
                            textBaseline: TextBaseline.alphabetic,
                            children: [
                              Text('${latest.avg.toStringAsFixed(1)}$unit',
                                  style: Theme.of(context)
                                      .textTheme
                                      .headlineMedium
                                      ?.copyWith(fontWeight: FontWeight.bold)),
                              const SizedBox(width: 8),
                              Text('now', style: Theme.of(context).textTheme.bodySmall),
                            ],
                          ),
                          Text(
                            'range ${minV.toStringAsFixed(1)}$unit – ${maxV.toStringAsFixed(1)}$unit',
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                          const SizedBox(height: 12),
                          Text(_rangeLabel,
                              style: Theme.of(context)
                                  .textTheme
                                  .titleSmall
                                  ?.copyWith(fontWeight: FontWeight.bold)),
                          const SizedBox(height: 12),
                          SizedBox(
                            height: 240,
                            child: _HistoryChart(
                              actual: actual,
                              predicted: data.predicted,
                              unit: unit,
                              axisTimeLabel: _axisTimeLabel,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _HistoryChart extends StatelessWidget {
  final List<HistoryPoint> actual;
  final List<HistoryPoint> predicted;
  final String unit;
  final String Function(DateTime) axisTimeLabel;

  const _HistoryChart({
    required this.actual,
    required this.predicted,
    required this.unit,
    required this.axisTimeLabel,
  });

  @override
  Widget build(BuildContext context) {
    final t0 = actual.first.time.millisecondsSinceEpoch.toDouble();
    double xFor(DateTime t) => (t.millisecondsSinceEpoch - t0) / 1000.0;

    final allValues = [
      ...actual.map((p) => p.min),
      ...actual.map((p) => p.max),
      ...predicted.map((p) => p.avg),
    ];
    final minV = allValues.reduce(math.min);
    final maxV = allValues.reduce(math.max);
    final range = (maxV - minV).abs().clamp(1.0, double.infinity);

    final minSpots = actual.map((p) => FlSpot(xFor(p.time), p.min)).toList();
    final maxSpots = actual.map((p) => FlSpot(xFor(p.time), p.max)).toList();
    final avgSpots = actual.map((p) => FlSpot(xFor(p.time), p.avg)).toList();

    final predSpots = <FlSpot>[
      if (predicted.isNotEmpty) FlSpot(xFor(actual.last.time), actual.last.avg),
      ...predicted.map((p) => FlSpot(xFor(p.time), p.avg)),
    ];

    final bars = <LineChartBarData>[
      LineChartBarData(
        spots: minSpots,
        color: Colors.transparent,
        barWidth: 0,
        dotData: const FlDotData(show: false),
      ),
      LineChartBarData(
        spots: maxSpots,
        color: Colors.transparent,
        barWidth: 0,
        dotData: const FlDotData(show: false),
        betweenBarsData: [
          BetweenBarsData(fromIndex: 1, toIndex: 0, color: AppColors.brandLight.withAlpha(40)),
        ],
      ),
      LineChartBarData(
        spots: avgSpots,
        color: AppColors.brandLight,
        barWidth: 2.5,
        dotData: const FlDotData(show: false),
      ),
      if (predSpots.length > 1)
        LineChartBarData(
          spots: predSpots,
          color: AppColors.brandLight.withAlpha(150),
          barWidth: 2,
          dashArray: const [6, 4],
          dotData: const FlDotData(show: false),
        ),
    ];
    final predictedBarIndex = predSpots.length > 1 ? bars.length - 1 : -1;
    final maxXRaw = predSpots.isNotEmpty ? predSpots.last.x : xFor(actual.last.time);
    final maxX = maxXRaw <= 0 ? 1.0 : maxXRaw;

    return LineChart(
      LineChartData(
        minX: 0,
        maxX: maxX,
        minY: minV,
        maxY: maxV,
        gridData: FlGridData(
          show: true,
          horizontalInterval: range / 4,
          getDrawingHorizontalLine: (_) =>
              FlLine(color: Theme.of(context).dividerColor.withAlpha(80), strokeWidth: 1),
        ),
        borderData: FlBorderData(show: false),
        lineBarsData: bars,
        titlesData: FlTitlesData(
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 46,
              interval: range / 4,
              getTitlesWidget: (value, meta) => Text(
                '${value.toStringAsFixed(1)}$unit',
                style: const TextStyle(fontSize: 10),
              ),
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 26,
              interval: maxX / 4,
              getTitlesWidget: (value, meta) => Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  axisTimeLabel(actual.first.time.add(Duration(seconds: value.round()))),
                  style: const TextStyle(fontSize: 10),
                ),
              ),
            ),
          ),
        ),
        lineTouchData: LineTouchData(
          touchTooltipData: LineTouchTooltipData(
            getTooltipItems: (spots) => spots.map((s) {
              final time = actual.first.time.add(Duration(seconds: s.x.round()));
              final isPrediction = s.barIndex == predictedBarIndex;
              final label = isPrediction
                  ? 'predicted · ${s.y.toStringAsFixed(1)}$unit'
                  : '${_timeLabel(time)} · ${s.y.toStringAsFixed(1)}$unit';
              return LineTooltipItem(label, const TextStyle(color: Colors.white, fontSize: 12));
            }).toList(),
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd app && flutter test test/widgets/history_screen_test.dart`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add app/pubspec.yaml app/pubspec.lock app/lib/screens/history/history_screen.dart app/test/widgets/history_screen_test.dart
git commit -m "feat: rebuild history chart with fl_chart -- axes, grid, tabs, range selector, predictions"
```

---

### Task 8: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Static analysis**

Run: `cd app && flutter analyze`
Expected: `No issues found!` (fix anything it flags before continuing)

- [ ] **Step 2: Full test suite**

Run: `cd app && flutter test`
Expected: all tests pass, including every test added in Tasks 1–7 plus the pre-existing suite.

- [ ] **Step 3: Release build smoke test**

Run: `cd app && flutter build apk --release`
Expected: `✓ Built build\app\outputs\flutter-apk\app-release.apk`

- [ ] **Step 4: Commit (only if Steps 1–3 required fixes)**

If any fixes were needed to make analyze/test/build pass cleanly:

```bash
git add -A
git commit -m "fix: address analyzer/test/build issues from history chart enhancement"
```

If no fixes were needed, skip this step — there is nothing to commit.
