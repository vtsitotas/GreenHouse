import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/providers/history_provider.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

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
          return Padding(
            padding: const EdgeInsets.all(16),
            child: Card(
              elevation: 4,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Last 24 Hours',
                        style: Theme.of(context).textTheme.titleSmall
                            ?.copyWith(fontWeight: FontWeight.bold)),
                    const SizedBox(height: 12),
                    SizedBox(
                      height: 220,
                      child: CustomPaint(
                        size: const Size(double.infinity, 220),
                        painter: _HistoryPainter(points: points),
                      ),
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
  const _HistoryPainter({required this.points});

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
  }

  @override
  bool shouldRepaint(_HistoryPainter old) => old.points != points;
}
