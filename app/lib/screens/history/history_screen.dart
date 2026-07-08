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
                                  key: const Key('current-value'),
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
        // Shades the area between the max line (index 1) and the min line
        // (index 0) to render the min-max band. This lives on LineChartData
        // in this fl_chart version, not on the individual LineChartBarData
        // (the brief's original placement, which was a slightly older API
        // shape).
        betweenBarsData: [
          BetweenBarsData(fromIndex: 1, toIndex: 0, color: AppColors.brandLight.withAlpha(40)),
        ],
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
