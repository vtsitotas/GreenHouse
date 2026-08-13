import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/theme/app_colors.dart';
import 'package:greenhouse_app/utils/last_seen_format.dart';
import 'package:greenhouse_app/utils/plain_language.dart';

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
    // local/remote/reconnecting wording comes from connectionStatusLabel() so
    // this banner and Settings' Connection row can never disagree about what
    // the same ConnectionStatus means -- they used to (this banner had its
    // own shorter "Connected" for `local` while Settings said "Connected on
    // your home network"), which is exactly the kind of drift a single
    // source of truth is meant to prevent.
    final (color, icon, label) = switch (status) {
      ConnectionStatus.local        => (AppColors.local,        Icons.wifi,  connectionStatusLabel(status)),
      ConnectionStatus.remote       => (AppColors.remote,       Icons.cloud, connectionStatusLabel(status)),
      ConnectionStatus.reconnecting => (AppColors.reconnecting, Icons.sync,  connectionStatusLabel(status)),
      // "showing last known data" begged the question the user actually has:
      // *how old is it?* Without an answer, an hour-old reading looks as
      // trustworthy as a fresh one. This needs lastSighting, which
      // connectionStatusLabel() doesn't take, so it stays bespoke here --
      // but the base phrase ("Not connected") still matches plain_language.
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
