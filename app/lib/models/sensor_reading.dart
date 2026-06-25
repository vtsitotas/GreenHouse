class SensorReading {
  final String zone;
  final String metric;
  final double value;
  final DateTime receivedAt;

  const SensorReading({
    required this.zone,
    required this.metric,
    required this.value,
    required this.receivedAt,
  });

  factory SensorReading.fromMqtt(String topic, String payload) {
    final parts = topic.split('/');
    final zone = parts[1];
    final metric = parts.sublist(2).join('/');
    final value = double.parse(payload.trim());
    return SensorReading(zone: zone, metric: metric, value: value, receivedAt: DateTime.now());
  }
}
