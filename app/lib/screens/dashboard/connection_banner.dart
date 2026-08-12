import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/theme/app_colors.dart';
import 'package:greenhouse_app/utils/last_seen_format.dart';

class ConnectionBanner extends StatelessWidget {
  final ConnectionStatus status;

  /// When the newest reading was actually taken, from
  /// `lastSensorSightingProvider`. Null means nothing has been confirmed live
  /// — usually because everything the app has seen so far was a retained MQTT
  /// replay, which carries no timestamp.
  final DateTime? lastSighting;

  const ConnectionBanner({required this.status, this.lastSighting, super.key});

  @override
  Widget build(BuildContext context) {
    final (color, icon, label) = switch (status) {
      ConnectionStatus.local        => (AppColors.local,        Icons.wifi,      'Connected'),
      ConnectionStatus.remote       => (AppColors.remote,       Icons.cloud,     'Connected from away'),
      ConnectionStatus.reconnecting => (AppColors.reconnecting, Icons.sync,      'Reconnecting…'),
      // "showing last known data" begged the question the user actually has:
      // *how old is it?* Without an answer, an hour-old reading looks as
      // trustworthy as a fresh one.
      ConnectionStatus.offline      => (
          AppColors.offline,
          Icons.wifi_off,
          lastSighting == null
              ? 'Not connected — no readings yet'
              : 'Not connected — showing readings from '
                  '${formatLastSeen(lastSighting)}',
        ),
    };
    return Container(
      width: double.infinity,
      color: color,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Row(children: [
        Icon(icon, size: 14, color: Colors.white),
        const SizedBox(width: 6),
        Expanded(
          child: Text(label,
              style: const TextStyle(color: Colors.white, fontSize: 12)),
        ),
      ]),
    );
  }
}
