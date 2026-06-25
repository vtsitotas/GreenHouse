class NodeStatus {
  final String nodeId;
  final bool isOnline;
  final double? batteryPercent;
  final DateTime lastSeen;

  const NodeStatus({
    required this.nodeId,
    required this.isOnline,
    this.batteryPercent,
    required this.lastSeen,
  });

  factory NodeStatus.fromMqttStatus(String nodeId, String payload) => NodeStatus(
        nodeId: nodeId,
        isOnline: payload.trim() == 'online',
        lastSeen: DateTime.now(),
      );

  factory NodeStatus.fromMqttBattery(String nodeId, String payload) => NodeStatus(
        nodeId: nodeId,
        isOnline: true,
        batteryPercent: double.tryParse(payload.trim()),
        lastSeen: DateTime.now(),
      );

  NodeStatus copyWith({bool? isOnline, double? batteryPercent, DateTime? lastSeen}) =>
      NodeStatus(
        nodeId: nodeId,
        isOnline: isOnline ?? this.isOnline,
        batteryPercent: batteryPercent ?? this.batteryPercent,
        lastSeen: lastSeen ?? this.lastSeen,
      );
}
