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
