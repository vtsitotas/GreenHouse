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
