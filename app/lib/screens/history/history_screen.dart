import 'dart:math' as math;
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

String _twoDigits(int n) => n.toString().padLeft(2, '0');
String _timeLabel(DateTime t) => '${_twoDigits(t.hour)}:${_twoDigits(t.minute)}';

class HistoryScreen extends ConsumerWidget {
  final String zone;
  final String metric;
  const HistoryScreen({required this.zone, required this.metric, super.key});

  String get _title {
    final zoneLabel = zone.startsWith('zone') ? 'Zone ${zone.substring(4)}' : zone;
    final metricLabel = metric.replaceAll('_', ' ');
    return '$zoneLabel — $metricLabel';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pointsAsync =
        ref.watch(historyPointsProvider(HistoryQuery(zone: zone, metric: metric)));
    final unit = _unitFor(metric);
    return Scaffold(
      appBar: AppBar(title: Text(_title)),
      body: pointsAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Text('Could not load history.\n$e', textAlign: TextAlign.center),
          ),
        ),
        data: (points) {
          if (points.isEmpty) {
            return const Center(child: Text('No history yet for this sensor.'));
          }
          final latest = points.last;
          final minV = points.map((p) => p.min).reduce(math.min);
          final maxV = points.map((p) => p.max).reduce(math.max);
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
                            style: Theme.of(context).textTheme.headlineMedium
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
                    Text('Last 24 Hours',
                        style: Theme.of(context).textTheme.titleSmall
                            ?.copyWith(fontWeight: FontWeight.bold)),
                    const SizedBox(height: 12),
                    SizedBox(
                      height: 220,
                      child: CustomPaint(
                        size: const Size(double.infinity, 220),
                        painter: _HistoryPainter(points: points, unit: unit),
                      ),
                    ),
                    const SizedBox(height: 4),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(_timeLabel(points.first.time),
                            style: Theme.of(context).textTheme.labelSmall),
                        Text(_timeLabel(points.last.time),
                            style: Theme.of(context).textTheme.labelSmall),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _HistoryPainter extends CustomPainter {
  final List<HistoryPoint> points;
  final String unit;
  const _HistoryPainter({required this.points, required this.unit});

  @override
  void paint(Canvas canvas, Size size) {
    if (points.isEmpty) return;
    final n = points.length;

    final minV = points.map((p) => p.min).reduce(math.min);
    final maxV = points.map((p) => p.max).reduce(math.max);
    final range = (maxV - minV).abs().clamp(1.0, double.infinity);

    double tx(int i) => n > 1 ? i * size.width / (n - 1) : size.width / 2;
    double ty(double v) => size.height - ((v - minV) / range) * size.height;

    final linePaint = Paint()
      ..color = AppColors.brandLight
      ..strokeWidth = 2.5
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final fillPaint = Paint()
      ..color = AppColors.brandLight.withAlpha(40)
      ..style = PaintingStyle.fill;

    final path = Path()..moveTo(tx(0), ty(points[0].avg));
    for (int i = 1; i < n; i++) {
      path.lineTo(tx(i), ty(points[i].avg));
    }
    final fill = Path.from(path)
      ..lineTo(tx(n - 1), size.height)
      ..lineTo(tx(0), size.height)
      ..close();
    canvas.drawPath(fill, fillPaint);
    canvas.drawPath(path, linePaint);

    _drawLabel(canvas, '${maxV.toStringAsFixed(1)}$unit', const Offset(4, 2));
    _drawLabel(canvas, '${minV.toStringAsFixed(1)}$unit', Offset(4, size.height - 16));
  }

  void _drawLabel(Canvas canvas, String text, Offset offset) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(color: Colors.grey, fontSize: 11),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(canvas, offset);
  }

  @override
  bool shouldRepaint(_HistoryPainter old) =>
      old.points != points || old.unit != unit;
}
