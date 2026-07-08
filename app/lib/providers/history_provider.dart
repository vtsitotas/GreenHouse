import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/services/history_service.dart';
import 'package:greenhouse_app/services/pairing_service.dart';
import 'package:greenhouse_app/utils/history_prediction.dart';

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
