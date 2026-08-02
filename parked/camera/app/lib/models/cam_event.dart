class CamEvent {
  final String eventId;
  final int ts; // unix seconds

  const CamEvent({required this.eventId, required this.ts});

  factory CamEvent.fromJson(Map<String, dynamic> json) => CamEvent(
        eventId: json['event_id'] as String,
        ts: (json['ts'] as num).toInt(),
      );

  DateTime get timestamp => DateTime.fromMillisecondsSinceEpoch(ts * 1000);
}
