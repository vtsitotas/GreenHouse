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
