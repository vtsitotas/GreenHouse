import 'package:greenhouse_app/models/cam_event.dart';

class CamStatus {
  final bool online;
  final double? lastSeen;
  final String? ip;
  final CamEvent? lastEvent;

  const CamStatus({required this.online, this.lastSeen, this.ip, this.lastEvent});

  factory CamStatus.fromJson(Map<String, dynamic> json) => CamStatus(
        online: json['online'] as bool? ?? false,
        lastSeen: (json['last_seen'] as num?)?.toDouble(),
        ip: json['ip'] as String?,
        lastEvent: json['last_event'] != null
            ? CamEvent.fromJson(json['last_event'] as Map<String, dynamic>)
            : null,
      );
}
