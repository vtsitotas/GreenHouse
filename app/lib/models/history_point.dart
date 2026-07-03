class HistoryPoint {
  final DateTime time;
  final double avg;
  final double min;
  final double max;

  const HistoryPoint({
    required this.time,
    required this.avg,
    required this.min,
    required this.max,
  });

  factory HistoryPoint.fromJson(List<dynamic> json) => HistoryPoint(
        time: DateTime.fromMillisecondsSinceEpoch((json[0] as num).toInt() * 1000),
        avg: (json[1] as num).toDouble(),
        min: (json[2] as num).toDouble(),
        max: (json[3] as num).toDouble(),
      );
}

class HistorySeries {
  final String kind;
  final String? zone;
  final String metric;

  const HistorySeries({required this.kind, required this.zone, required this.metric});

  factory HistorySeries.fromJson(Map<String, dynamic> json) => HistorySeries(
        kind: json['kind'] as String,
        zone: json['zone'] as String?,
        metric: json['metric'] as String,
      );
}
