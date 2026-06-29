import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:greenhouse_app/models/weather_alert.dart';
import 'package:greenhouse_app/models/weather_rule.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

// ── WeatherScreen ────────────────────────────────────────────────────────────

class WeatherScreen extends ConsumerStatefulWidget {
  const WeatherScreen({super.key});
  @override
  ConsumerState<WeatherScreen> createState() => _WeatherScreenState();
}

class _WeatherScreenState extends ConsumerState<WeatherScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 2, vsync: this);
    // Request rules from Pi on open
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(repositoryProvider).requestRules();
    });
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  Future<void> _showLocationDialog(BuildContext context) async {
    final latCtrl = TextEditingController();
    final lonCtrl = TextEditingController();
    final formKey = GlobalKey<FormState>();
    bool gpsLoading = false;
    int intervalSeconds = 1800;

    const intervalOptions = [
      (30,    'Every 30 sec (testing)'),
      (300,   'Every 5 min'),
      (900,   'Every 15 min'),
      (1800,  'Every 30 min (default)'),
      (3600,  'Every 1 hour'),
    ];

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: const Text('Set Weather Location'),
          content: Form(
            key: formKey,
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              OutlinedButton.icon(
                icon: gpsLoading
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.my_location, size: 18),
                label: Text(gpsLoading ? 'Getting location…' : 'Use current GPS location'),
                onPressed: gpsLoading ? null : () async {
                  setDialogState(() => gpsLoading = true);
                  try {
                    LocationPermission perm = await Geolocator.checkPermission();
                    if (perm == LocationPermission.denied) {
                      perm = await Geolocator.requestPermission();
                    }
                    if (perm == LocationPermission.deniedForever) {
                      if (ctx.mounted) {
                        ScaffoldMessenger.of(ctx).showSnackBar(
                          const SnackBar(content: Text('Location permission denied. Enable it in phone settings.')),
                        );
                      }
                      return;
                    }
                    final pos = await Geolocator.getCurrentPosition(
                      locationSettings: const LocationSettings(accuracy: LocationAccuracy.low),
                    );
                    latCtrl.text = pos.latitude.toStringAsFixed(4);
                    lonCtrl.text = pos.longitude.toStringAsFixed(4);
                  } catch (e) {
                    if (ctx.mounted) {
                      ScaffoldMessenger.of(ctx).showSnackBar(
                        SnackBar(content: Text('GPS error: $e')),
                      );
                    }
                  } finally {
                    setDialogState(() => gpsLoading = false);
                  }
                },
              ),
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 10),
                child: Row(children: [
                  Expanded(child: Divider()),
                  Padding(padding: EdgeInsets.symmetric(horizontal: 8), child: Text('or enter manually', style: TextStyle(fontSize: 11, color: Colors.grey))),
                  Expanded(child: Divider()),
                ]),
              ),
              TextFormField(
                controller: latCtrl,
                decoration: const InputDecoration(labelText: 'Latitude', hintText: '37.97'),
                keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                validator: (v) => double.tryParse(v ?? '') == null ? 'Enter a number' : null,
              ),
              const SizedBox(height: 8),
              TextFormField(
                controller: lonCtrl,
                decoration: const InputDecoration(labelText: 'Longitude', hintText: '23.72'),
                keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                validator: (v) => double.tryParse(v ?? '') == null ? 'Enter a number' : null,
              ),
              const SizedBox(height: 16),
              DropdownButtonFormField<int>(
                value: intervalSeconds,
                decoration: const InputDecoration(labelText: 'Fetch interval', border: OutlineInputBorder()),
                items: intervalOptions.map((o) => DropdownMenuItem(
                  value: o.$1,
                  child: Text(o.$2),
                )).toList(),
                onChanged: (v) => setDialogState(() => intervalSeconds = v ?? 1800),
              ),
            ]),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(
              onPressed: () {
                if (formKey.currentState!.validate()) Navigator.pop(ctx, true);
              },
              child: const Text('Save'),
            ),
          ],
        ),
      ),
    );

    if (confirmed == true && mounted) {
      final lat = double.parse(latCtrl.text);
      final lon = double.parse(lonCtrl.text);
      await ref.read(repositoryProvider).publishLocation(lat, lon, intervalSeconds: intervalSeconds);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Settings saved. Will apply on the next weather cycle.')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Weather & Automation'),
        actions: [
          IconButton(
            icon: const Icon(Icons.location_on_outlined),
            tooltip: 'Set location',
            onPressed: () => _showLocationDialog(context),
          ),
        ],
        bottom: TabBar(
          controller: _tabs,
          indicatorColor: Colors.white,
          labelColor: Colors.white,
          unselectedLabelColor: Colors.white70,
          tabs: const [
            Tab(icon: Icon(Icons.cloud), text: 'Forecast'),
            Tab(icon: Icon(Icons.rule), text: 'Rules'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: const [
          _ForecastTab(),
          _RulesTab(),
        ],
      ),
    );
  }
}

// ── Forecast Tab ─────────────────────────────────────────────────────────────

class _ForecastTab extends ConsumerWidget {
  const _ForecastTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final forecastAsync = ref.watch(forecastProvider);
    final readingsAsync = ref.watch(readingsProvider);

    final weatherReadings = readingsAsync.valueOrNull?['weather'] ?? {};

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // Current conditions card
        _CurrentWeatherCard(readings: weatherReadings),
        const SizedBox(height: 12),
        // 24h forecast chart
        forecastAsync.when(
          loading: () => const Center(
            child: Padding(
              padding: EdgeInsets.all(32),
              child: CircularProgressIndicator(),
            ),
          ),
          error: (e, _) => Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: Text('No forecast data yet.\nWait for the Pi to publish.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.grey[600])),
            ),
          ),
          data: (forecast) => _ForecastChart(forecast: forecast),
        ),
        const SizedBox(height: 12),
        // Recent alerts
        const _RecentAlertsCard(),
      ],
    );
  }
}

// ── Current Weather Card ─────────────────────────────────────────────────────

class _CurrentWeatherCard extends StatelessWidget {
  final Map<String, double> readings;
  const _CurrentWeatherCard({required this.readings});

  @override
  Widget build(BuildContext context) {
    final temp   = readings['temperature'];
    final hum    = readings['humidity'];
    final wind   = readings['wind_kmh'];
    final rain   = readings['rain_mm_1h'];
    final uv     = readings['uv_index'];

    return Card(
      elevation: 4,
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              const Icon(Icons.wb_cloudy_outlined, size: 28, color: AppColors.brand),
              const SizedBox(width: 8),
              Text('Outside Conditions',
                  style: Theme.of(context).textTheme.titleMedium
                      ?.copyWith(fontWeight: FontWeight.bold)),
              const Spacer(),
              if (readings.isEmpty)
                const Text('Waiting…', style: TextStyle(color: Colors.grey)),
            ]),
            const Divider(height: 20),
            Wrap(spacing: 24, runSpacing: 12, children: [
              if (temp != null)   _BigMetric(Icons.thermostat,   '${temp.toStringAsFixed(1)} °C',  'Temperature'),
              if (hum  != null)   _BigMetric(Icons.water_drop,   '${hum.toStringAsFixed(0)} %',    'Humidity'),
              if (wind != null)   _BigMetric(Icons.air,          '${wind.toStringAsFixed(1)} km/h','Wind'),
              if (rain != null)   _BigMetric(Icons.umbrella,     '${rain.toStringAsFixed(2)} mm',  'Rain/hr'),
              if (uv   != null)   _BigMetric(Icons.wb_sunny,     uv.toStringAsFixed(1),            'UV Index'),
            ]),
            if (readings.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 12),
                child: Text('Weather data will appear after the Pi fetches the first forecast.',
                    style: TextStyle(color: Colors.grey, fontSize: 13)),
              ),
          ],
        ),
      ),
    );
  }
}

class _BigMetric extends StatelessWidget {
  final IconData icon;
  final String value;
  final String label;
  const _BigMetric(this.icon, this.value, this.label);

  @override
  Widget build(BuildContext context) => Column(
        children: [
          Icon(icon, color: AppColors.brand, size: 22),
          const SizedBox(height: 4),
          Text(value,
              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
          Text(label,
              style: TextStyle(fontSize: 11, color: Colors.grey[600])),
        ],
      );
}

// ── Forecast Chart ───────────────────────────────────────────────────────────

class _ForecastChart extends StatelessWidget {
  final Map<String, dynamic> forecast;
  const _ForecastChart({required this.forecast});

  @override
  Widget build(BuildContext context) {
    final times  = (forecast['times']  as List?)?.cast<String>() ?? [];
    final temps  = (forecast['temps']  as List?)?.cast<num>()   ?? [];
    final precip = (forecast['precip'] as List?)?.cast<num>()   ?? [];

    if (times.isEmpty) return const SizedBox.shrink();

    return Card(
      elevation: 4,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('24-Hour Forecast',
                style: Theme.of(context).textTheme.titleSmall
                    ?.copyWith(fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            const Row(children: [
              _LegendDot(AppColors.brandLight, 'Temp (°C)'),
              SizedBox(width: 16),
              _LegendDot(Colors.lightBlue, 'Rain (mm)'),
            ]),
            const SizedBox(height: 12),
            SizedBox(
              height: 140,
              child: CustomPaint(
                size: const Size(double.infinity, 140),
                painter: _ForecastPainter(
                  temps: temps.map((e) => e.toDouble()).toList(),
                  precip: precip.map((e) => e.toDouble()).toList(),
                ),
              ),
            ),
            const SizedBox(height: 6),
            // Hour labels (every 4h)
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                for (int i = 0; i < times.length && i < 24; i += 4)
                  Text(
                    _hourLabel(times[i]),
                    style: const TextStyle(fontSize: 10, color: Colors.grey),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  String _hourLabel(String iso) {
    try {
      final dt = DateTime.parse(iso);
      return '${dt.hour.toString().padLeft(2, '0')}:00';
    } catch (_) {
      return iso.length >= 13 ? iso.substring(11, 13) : '';
    }
  }
}

class _LegendDot extends StatelessWidget {
  final Color color;
  final String label;
  const _LegendDot(this.color, this.label);
  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 10, height: 10, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
          const SizedBox(width: 4),
          Text(label, style: const TextStyle(fontSize: 11)),
        ],
      );
}

class _ForecastPainter extends CustomPainter {
  final List<double> temps;
  final List<double> precip;
  const _ForecastPainter({required this.temps, required this.precip});

  @override
  void paint(Canvas canvas, Size size) {
    if (temps.isEmpty) return;
    final n = math.min(temps.length, 24);

    // ── Rain bars ──────────────────────────────────────────────────────────
    final maxRain = precip.fold<double>(0, math.max);
    final rainPaint = Paint()
      ..color = Colors.lightBlue.withAlpha(180)
      ..style = PaintingStyle.fill;
    final barWidth = size.width / n * 0.6;
    for (int i = 0; i < n; i++) {
      final barH = maxRain > 0 ? (precip[i] / maxRain) * size.height * 0.4 : 0.0;
      final x = (i + 0.5) * size.width / n;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(x - barWidth / 2, size.height - barH, barWidth, barH),
          const Radius.circular(3),
        ),
        rainPaint,
      );
    }

    // ── Temperature line ───────────────────────────────────────────────────
    final minT = temps.fold<double>(temps[0], math.min);
    final maxT = temps.fold<double>(temps[0], math.max);
    final range = (maxT - minT).abs().clamp(1.0, double.infinity);

    double tx(int i) => (i + 0.5) * size.width / n;
    double ty(double v) => size.height * 0.55 - ((v - minT) / range) * size.height * 0.5;

    final linePaint = Paint()
      ..color = AppColors.brandLight
      ..strokeWidth = 2.5
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final fillPaint = Paint()
      ..color = AppColors.brandLight.withAlpha(40)
      ..style = PaintingStyle.fill;

    final path = Path()..moveTo(tx(0), ty(temps[0]));
    for (int i = 1; i < n; i++) {
      path.lineTo(tx(i), ty(temps[i]));
    }
    // Fill area under line
    final fill = Path.from(path)
      ..lineTo(tx(n - 1), size.height)
      ..lineTo(tx(0), size.height)
      ..close();
    canvas.drawPath(fill, fillPaint);
    canvas.drawPath(path, linePaint);

    // Dots at each point
    final dotPaint = Paint()..color = AppColors.brand..style = PaintingStyle.fill;
    for (int i = 0; i < n; i++) {
      canvas.drawCircle(Offset(tx(i), ty(temps[i])), 2.5, dotPaint);
    }
  }

  @override
  bool shouldRepaint(_ForecastPainter old) =>
      old.temps != temps || old.precip != precip;
}

// ── Recent Alerts Card ───────────────────────────────────────────────────────

class _RecentAlertsCard extends ConsumerStatefulWidget {
  const _RecentAlertsCard();
  @override
  ConsumerState<_RecentAlertsCard> createState() => _RecentAlertsCardState();
}

class _RecentAlertsCardState extends ConsumerState<_RecentAlertsCard> {
  final List<WeatherAlert> _recent = [];

  @override
  void initState() {
    super.initState();
    ref.listenManual(weatherAlertsProvider, (_, next) {
      next.whenData((alert) {
        if (mounted) setState(() => _recent.insert(0, alert));
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_recent.isEmpty) {
      return Card(
        elevation: 2,
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Row(children: [
            Icon(Icons.notifications_none, color: Colors.grey[400]),
            const SizedBox(width: 8),
            const Text('No alerts received yet.', style: TextStyle(color: Colors.grey)),
          ]),
        ),
      );
    }
    return Card(
      elevation: 2,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 4),
            child: Text('Recent Alerts',
                style: Theme.of(context).textTheme.titleSmall
                    ?.copyWith(fontWeight: FontWeight.bold)),
          ),
          ..._recent.take(5).map((a) => ListTile(
                dense: true,
                leading: Icon(
                  a.isWarning ? Icons.warning_amber_rounded : Icons.info_outline,
                  color: a.isWarning ? AppColors.warning : AppColors.brand,
                  size: 20,
                ),
                title: Text(a.title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                subtitle: Text(a.message, maxLines: 2, overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 12)),
              )),
        ],
      ),
    );
  }
}

// ── Rules Tab ────────────────────────────────────────────────────────────────

class _RulesTab extends ConsumerWidget {
  const _RulesTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final rulesAsync = ref.watch(rulesProvider);
    return rulesAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.rule_folder_outlined, size: 48, color: Colors.grey[400]),
              const SizedBox(height: 12),
              const Text('No rules received yet.\nPublish a rules-get request or wait for the Pi.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.grey)),
            ],
          ),
        ),
      ),
      data: (rules) => _RulesList(rules: rules),
    );
  }
}

class _RulesList extends ConsumerStatefulWidget {
  final List<WeatherRule> rules;
  const _RulesList({required this.rules});
  @override
  ConsumerState<_RulesList> createState() => _RulesListState();
}

class _RulesListState extends ConsumerState<_RulesList> {
  late List<WeatherRule> _rules;

  @override
  void initState() {
    super.initState();
    _rules = List.from(widget.rules);
  }

  @override
  void didUpdateWidget(_RulesList old) {
    super.didUpdateWidget(old);
    if (old.rules != widget.rules) {
      setState(() => _rules = List.from(widget.rules));
    }
  }

  void _toggle(int index, bool enabled) {
    final updated = List<WeatherRule>.from(_rules);
    updated[index] = updated[index].copyWith(enabled: enabled);
    setState(() => _rules = updated);
    ref.read(repositoryProvider).publishRules(updated);
  }

  void _editThreshold(int index) async {
    final rule = _rules[index];
    final ctrl = TextEditingController(text: rule.value.toString());
    final result = await showDialog<double>(
      context: context,
      builder: (_) => AlertDialog(
        title: Text('Edit: ${rule.name}'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Condition: ${rule.metricLabel} ${rule.op}', style: const TextStyle(fontSize: 13)),
            const SizedBox(height: 8),
            TextField(
              controller: ctrl,
              keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
              decoration: const InputDecoration(labelText: 'Threshold value', border: OutlineInputBorder()),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            onPressed: () {
              final v = double.tryParse(ctrl.text);
              if (v != null) Navigator.pop(context, v);
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (result != null) {
      final updated = List<WeatherRule>.from(_rules);
      updated[index] = updated[index].copyWith(value: result);
      setState(() => _rules = updated);
      ref.read(repositoryProvider).publishRules(updated);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Row(children: [
            const Icon(Icons.info_outline, size: 16, color: Colors.grey),
            const SizedBox(width: 6),
            Expanded(
              child: Text('Changes sync to the Pi immediately.',
                  style: TextStyle(fontSize: 12, color: Colors.grey[600])),
            ),
            TextButton.icon(
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Refresh'),
              onPressed: () => ref.read(repositoryProvider).requestRules(),
            ),
          ]),
        ),
        Expanded(
          child: ListView.builder(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            itemCount: _rules.length,
            itemBuilder: (context, i) => _RuleCard(
              rule: _rules[i],
              onToggle: (v) => _toggle(i, v),
              onEdit: () => _editThreshold(i),
            ),
          ),
        ),
      ],
    );
  }
}

class _RuleCard extends StatelessWidget {
  final WeatherRule rule;
  final ValueChanged<bool> onToggle;
  final VoidCallback onEdit;
  const _RuleCard({required this.rule, required this.onToggle, required this.onEdit});

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        leading: CircleAvatar(
          backgroundColor: rule.enabled
              ? AppColors.brand.withAlpha(30)
              : Colors.grey.withAlpha(30),
          child: Icon(
            _ruleIcon(rule.triggerMetric),
            color: rule.enabled ? AppColors.brand : Colors.grey,
            size: 20,
          ),
        ),
        title: Text(rule.name,
            style: TextStyle(
              fontWeight: FontWeight.w600,
              color: rule.enabled ? null : Colors.grey,
            )),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('When ${rule.metricLabel} ${rule.op} ${rule.value}',
                  style: const TextStyle(fontSize: 12)),
              Text('→ ${rule.actuatorId}: ${rule.command}',
                  style: TextStyle(fontSize: 12, color: Colors.grey[600])),
            ],
          ),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            IconButton(
              icon: const Icon(Icons.edit_outlined, size: 18),
              tooltip: 'Edit threshold',
              onPressed: onEdit,
            ),
            Switch(
              value: rule.enabled,
              onChanged: onToggle,
              activeThumbColor: AppColors.brand,
            ),
          ],
        ),
        isThreeLine: true,
      ),
    );
  }

  IconData _ruleIcon(String metric) {
    switch (metric) {
      case 'temperature': return Icons.thermostat;
      case 'rain_mm_1h':  return Icons.umbrella;
      case 'humidity':    return Icons.water_drop;
      case 'wind_kmh':    return Icons.air;
      case 'uv_index':    return Icons.wb_sunny;
      default:            return Icons.rule;
    }
  }
}
