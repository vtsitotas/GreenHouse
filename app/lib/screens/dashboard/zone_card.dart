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
    return GestureDetector(
      onTap: () => context.push('/history/$zone/air_temperature'),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Text(_title, style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)),
              if (lowSoil) ...[
                const SizedBox(width: 8),
                const Icon(Icons.warning_amber_rounded, color: AppColors.warning, size: 18),
              ],
              const Spacer(),
              Icon(Icons.chevron_right, color: Colors.grey[400]),
            ]),
            const SizedBox(height: 12),
            Wrap(spacing: 16, runSpacing: 8, children: [
              if (readings['air/temperature'] != null)
                _Chip(Icons.thermostat, 'Temp', '${readings['air/temperature']!.toStringAsFixed(1)} °C'),
              if (readings['air/humidity'] != null)
                _Chip(Icons.water_drop, 'Humidity', '${readings['air/humidity']!.toStringAsFixed(0)} %'),
              if (soil != null)
                _Chip(Icons.grass, 'Soil', '${soil.toStringAsFixed(0)} %', color: lowSoil ? AppColors.warning : null),
              if (readings['light/lux'] != null)
                _Chip(Icons.wb_sunny, 'Light', '${readings['light/lux']!.toStringAsFixed(0)} lux'),
              if (readings['pressure'] != null)
                _Chip(Icons.speed, 'Pressure', '${readings['pressure']!.toStringAsFixed(0)} hPa'),
            ]),
          ]),
        ),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final Color? color;
  const _Chip(this.icon, this.label, this.value, {this.color});

  @override
  Widget build(BuildContext context) => Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 16, color: color ?? Theme.of(context).colorScheme.primary),
        const SizedBox(width: 4),
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: Theme.of(context).textTheme.labelSmall),
          Text(value, style: TextStyle(fontWeight: FontWeight.w600, color: color)),
        ]),
      ]);
}
