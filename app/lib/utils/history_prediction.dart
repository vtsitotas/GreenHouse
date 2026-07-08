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
