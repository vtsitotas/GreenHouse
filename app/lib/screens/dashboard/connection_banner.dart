import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class ConnectionBanner extends StatelessWidget {
  final ConnectionStatus status;
  const ConnectionBanner({required this.status, super.key});

  @override
  Widget build(BuildContext context) {
    final (color, icon, label) = switch (status) {
      ConnectionStatus.local        => (AppColors.local,        Icons.wifi,      'Local'),
      ConnectionStatus.remote       => (AppColors.remote,       Icons.cloud,     'Remote'),
      ConnectionStatus.reconnecting => (AppColors.reconnecting, Icons.sync,      'Reconnecting…'),
      ConnectionStatus.offline      => (AppColors.offline,      Icons.wifi_off,  'Offline — showing last known data'),
    };
    return Container(
      width: double.infinity,
      color: color,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Row(children: [
        Icon(icon, size: 14, color: Colors.white),
        const SizedBox(width: 6),
        Text(label, style: const TextStyle(color: Colors.white, fontSize: 12)),
      ]),
    );
  }
}
