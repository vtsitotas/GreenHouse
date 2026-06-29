import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

/// Dashboard card showing current outside weather from greenhouse/weather/* topics.
class WeatherCard extends ConsumerWidget {
  const WeatherCard({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final readingsAsync = ref.watch(readingsProvider);
    final weather = readingsAsync.valueOrNull?['weather'];

    // Don't render the card at all until we have at least one weather metric
    if (weather == null || weather.isEmpty) return const SizedBox.shrink();

    final temp = weather['temperature'];
    final rain = weather['rain_mm_1h'];
    final wind = weather['wind_kmh'];
    final hum  = weather['humidity'];
    final uv   = weather['uv_index'];

    final hasRain = rain != null && rain > 0.1;
    final isFrost = temp != null && temp < 3;
    final isHot   = temp != null && temp > 35;

    Color cardColor = AppColors.brand;
    IconData mainIcon = Icons.wb_cloudy_outlined;
    if (hasRain)  { cardColor = Colors.blueGrey; mainIcon = Icons.umbrella; }
    if (isFrost)  { cardColor = const Color(0xFF1565C0); mainIcon = Icons.ac_unit; }
    if (isHot)    { cardColor = Colors.deepOrange; mainIcon = Icons.wb_sunny; }

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      elevation: 3,
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: InkWell(
        onTap: () => context.go('/weather'),
        child: Container(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors: [cardColor, cardColor.withAlpha(200)],
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
            ),
          ),
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Icon(mainIcon, color: Colors.white, size: 22),
                const SizedBox(width: 8),
                const Text('Outside',
                    style: TextStyle(color: Colors.white70, fontSize: 13, fontWeight: FontWeight.w500)),
                const Spacer(),
                if (isFrost)
                  const _AlertBadge('Frost', Colors.lightBlue),
                if (hasRain && !isFrost)
                  const _AlertBadge('Rain', Colors.lightBlue),
                if (isHot)
                  const _AlertBadge('Heat', Colors.orange),
                const SizedBox(width: 4),
                const Icon(Icons.arrow_forward_ios, color: Colors.white54, size: 14),
              ]),
              const SizedBox(height: 10),
              Row(children: [
                if (temp != null) ...[
                  Text('${temp.toStringAsFixed(1)} °C',
                      style: const TextStyle(
                          color: Colors.white,
                          fontSize: 28,
                          fontWeight: FontWeight.bold,
                          letterSpacing: -0.5)),
                  const SizedBox(width: 16),
                ],
                Wrap(spacing: 14, children: [
                  if (hum  != null) _WeatherChip(Icons.water_drop, '${hum.toStringAsFixed(0)}%'),
                  if (wind != null) _WeatherChip(Icons.air,        '${wind.toStringAsFixed(1)} km/h'),
                  if (rain != null && rain > 0) _WeatherChip(Icons.umbrella, '${rain.toStringAsFixed(1)} mm/h'),
                  if (uv   != null) _WeatherChip(Icons.wb_sunny,   'UV ${uv.toStringAsFixed(1)}'),
                ]),
              ]),
            ],
          ),
        ),
      ),
    );
  }
}

class _WeatherChip extends StatelessWidget {
  final IconData icon;
  final String text;
  const _WeatherChip(this.icon, this.text);

  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: Colors.white70, size: 13),
          const SizedBox(width: 3),
          Text(text, style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w500)),
        ],
      );
}

class _AlertBadge extends StatelessWidget {
  final String label;
  final Color color;
  const _AlertBadge(this.label, this.color);

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.only(right: 4),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        decoration: BoxDecoration(color: color.withAlpha(200), borderRadius: BorderRadius.circular(10)),
        child: Text(label, style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.bold)),
      );
}
